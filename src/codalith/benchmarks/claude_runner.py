"""Run isolated native Claude Code comparisons over public Codalith MCP."""

from __future__ import annotations

import functools
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, TextIO

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codalith.benchmarks.agent_runner import (
    _ARMS,
    _BASE_SYSTEM,
    _FINAL_SCHEMA,
    _MCP_TOOLS,
    _audit_citations,
    _coverage,
    _hash,
    _issued_sources,
    _load_skill,
    _load_tasks,
    _output_path,
    _parse_final,
    _schedule,
    _Skill,
    _snapshot,
    _utc,
    _write,
)
from codalith.benchmarks.claude_events import (
    CLAUDE_USAGE_FIELDS,
    normalize_claude_mcp_result,
    parse_claude_events,
)
from codalith.benchmarks.native_runner import (
    _OWNERSHIP_LABEL,
    _events,
    _exception_text,
    _frozen,
    _mount,
    _run_container,
)
from codalith.benchmarks.native_runner import (
    _row as _native_row,
)

_CLAUDE_SYSTEM = (
    _BASE_SYSTEM + " Source evidence must come only from Codalith MCP. "
    "You may read a discovered skill. Do not read authentication files or other runtime "
    "materials, and do not use other network sources to obtain evidence."
)
_MODEL_NAMES = frozenset({"glm-5.3", "glm-5.3[1m]"})
_USAGE_FIELDS = (*CLAUDE_USAGE_FIELDS, "total_input_tokens", "total_tokens")


def claude_config_overrides() -> dict[str, Any]:
    """Non-secret controls supplied once through the native settings flag."""
    return {
        "disableAllHooks": True, "disableBundledSkills": True,
        "skillOverrides": {"doctor": "off", "plugin-authoring": "off"}, "autoMemoryEnabled": False,
        "enabledPlugins": {"cc-plugin-agents-md@builtin": False, "cc-plugin-plugin-authoring@builtin": False},
        "disableClaudeAiConnectors": True, "syncClaudeAiPlugins": False,
        "permissions": {
            "blockReadsOutsideWorkingDirectories": True,
            "allow": ["Read", "Skill(codalith-source)", "mcp__codalith__*"],
            "deny": ["Read(//run/provider-settings.json)", "Read(//proc/**)", "Read(//claude-home/**)", "Read(//tmp/home/**)"],
        },
    }


def _container_command(
    *, image: str, model: str, effort: str, endpoint: str, mcp_container: str,
    settings_file: Path, workspace: Path, container_name: str, ownership: str,
) -> list[str]:
    mcp_config = {"mcpServers": {"codalith": {"type": "http", "url": endpoint}}}
    return [
        "docker", "run", "--interactive", "--pull", "never", "--name", container_name,
        "--label", f"{_OWNERSHIP_LABEL}={ownership}", "--rm", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev", "--tmpfs", "/claude-home:rw,nosuid,nodev",
        "--tmpfs", "/home/agent:rw,nosuid,nodev", "--network", f"container:{mcp_container}",
        "--env", "HOME=/tmp/home", "--env", "CLAUDE_CONFIG_DIR=/claude-home",
        "--workdir", "/workspace", "--mount", _mount(workspace, "/workspace"),
        "--mount", _mount(settings_file, "/run/provider-settings.json"), image,
        "--tools", "Read,Skill", "--allowedTools", "Read,Skill,mcp__codalith__*",
        "--setting-sources", "project", "--settings", json.dumps(claude_config_overrides()),
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--strict-mcp-config", "--mcp-config", json.dumps(mcp_config),
        "--model", model, "--effort", effort, "--print", "--verbose",
        "--output-format", "stream-json", "--json-schema", json.dumps(_FINAL_SCHEMA),
        "--no-session-persistence", "--no-chrome", "--prompt-suggestions", "false",
        "--append-system-prompt", _CLAUDE_SYSTEM,
    ]


def _row(task: dict[str, Any], repeat: int, arm: str) -> dict[str, Any]:
    row = _native_row(task, repeat, arm)
    for field in ("outer_turns_started", "outer_turns_completed", "outer_turns_failed", "native_events", "native_command_review"):
        row.pop(field)
    row.update({
        "usage": dict.fromkeys(_USAGE_FIELDS), "usage_raw": None, "model_usage": None,
        "claude_num_turns": None, "duration_ms": None, "duration_api_ms": None,
        "total_cost_usd": None, "read_calls": 0, "skill_calls": 0,
        "native_builtin_review": [], "claude_events": None, "infrastructure_failure": False,
    })
    return row


def _provider_stat(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _frozen_provider(path: Path, expected: dict[str, int]) -> None:
    if _provider_stat(path) != expected:
        raise RuntimeError("Frozen Claude provider settings file metadata changed")


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for part in value for text in _texts(part)]
    if isinstance(value, dict):
        return [text for key in ("text", "content", "file") if key in value for text in _texts(value[key])]
    return []


def _normalized(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"(?m)^\s*\d+(?:→|\t)", "", text)


def _observe(row: dict[str, Any], parsed: dict[str, Any], skill: _Skill, skill_bytes: bytes) -> None:
    issued: dict[str, dict[str, Any]] = {}
    violations = row["native_policy_violations"]
    for item in parsed["mcp_items"]:
        row["mcp_calls"] += 1
        known = item.get("server") == "codalith" and item.get("tool") in _MCP_TOOLS
        if not known:
            row["unknown_calls"] += 1
            violations.append({"kind": "unknown_mcp", "item": item})
        else:
            row["status_calls" if item["tool"] == "codalith_status" else "source_calls"] += 1
        payload = normalize_claude_mcp_result(item)
        if payload is None:
            row["tool_errors"] += 1
        elif known and item.get("parent_tool_use_id") is None:
            _issued_sources(payload, issued)
    row["issued_sources"] = issued
    body = _normalized(skill_bytes.decode("utf-8"))
    expanded_body = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.DOTALL).strip()
    candidate_path = f".claude/skills/{skill.name}/SKILL.md"
    successful_skill = False
    for item in [*parsed["read_items"], *parsed["skill_items"]]:
        read = item.get("name") == "Read"
        row["read_calls" if read else "skill_calls"] += 1
        arguments = item.get("arguments") or {}
        candidate = (
            isinstance(arguments, dict) and
            (arguments.get("file_path") in {candidate_path, "./" + candidate_path, "/workspace/" + candidate_path}
             if read else arguments.get("skill") == skill.name)
        )
        row["skill_reads"] += int(candidate)
        success = candidate and row["arm"] == "skill_mcp" and item.get("status") == "completed" and item.get("parent_tool_use_id") is None
        if not read and success:
            successful_skill = True
        outputs = [_normalized(text) for value in (item.get("native_result"), item.get("tool_result", {}).get("content")) for text in _texts(value)]
        full = bool(success and any(body in text or expanded_body and expanded_body in text for text in outputs))
        partial = bool(success and not full and any(text.strip() and text.strip() in body for text in outputs))
        if full:
            row["skill_read_successes"] += 1
            row["skill_activation"] = "observed"
        elif partial and row["skill_activation"] != "observed":
            row["skill_activation"] = "partial_observed"
        row["native_builtin_review"].append({
            "classification": "candidate_read" if candidate and read else "candidate_skill" if candidate else "review_required",
            "skill_body_evidence": "full" if full else "partial" if partial else "not_observed", "item": item,
        })
    if successful_skill:
        for event in parsed["user_messages"]:
            if event.get("parent_tool_use_id") is not None:
                continue
            content = event["message"].get("content")
            if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
                continue
            for text in _texts(content):
                normalized = _normalized(text)
                if expanded_body and expanded_body in normalized and (f".claude/skills/{skill.name}" in normalized or f"<command-name>{skill.name}" in normalized):
                    row["skill_activation"] = "observed"
                    row["skill_read_successes"] += 1
                    row["native_builtin_review"].append({"classification": "expanded_skill", "skill_body_evidence": "full", "event": event})
                    break
    for item in parsed["other_items"]:
        if item.get("name") != "StructuredOutput":
            violations.append({"kind": "unexpected_builtin", "item": item})
    if parsed["subagent_events"]:
        violations.append({"kind": "subagent_activity", "events": parsed["subagent_events"]})


def _runtime_errors(parsed: dict[str, Any], requested_model: str, expected_skills: list[str]) -> list[str]:
    errors: list[str] = []
    if not parsed["init_messages"]:
        errors.append("Claude init event was not observed")
    names: list[Any] = [event.get("model") for event in parsed["init_messages"]]
    names.extend(event.get("message", {}).get("model") for event in parsed["assistant_messages"])
    if isinstance(parsed["model_usage"], dict):
        names.extend(parsed["model_usage"])
    elif parsed["completed"]:
        errors.append("Claude final modelUsage was not observed")
    if any(not isinstance(name, str) or name.lower() not in _MODEL_NAMES for name in names):
        errors.append(f"Claude reported an unexpected model route for requested {requested_model}")
    for event in parsed["init_messages"]:
        if event.get("skills") != expected_skills:
            errors.append("Claude initialized an unexpected skill catalog for its study arm")
        servers = event.get("mcp_servers")
        if not isinstance(servers, list) or len(servers) != 1 or servers[0].get("name") != "codalith" or servers[0].get("status") != "connected":
            errors.append("Claude did not initialize exactly the connected Codalith MCP server")
        tools = event.get("tools")
        required = {f"mcp__codalith__{name}" for name in _MCP_TOOLS} | {"Read", "Skill"}
        if not isinstance(tools, list) or not required.issubset(tools) or set(tools) - required - {"Read", "Skill", "StructuredOutput"}:
            errors.append("Claude initialized an unexpected native or MCP tool catalog")
        if event.get("plugins") or event.get("plugin_errors") or event.get("mcp_server_errors"):
            errors.append("Claude runtime customization or MCP initialization error was observed")
    for retry in parsed["api_retries"]:
        if retry.get("error_status") in {401, 403} or retry.get("error") in {"authentication_failed", "oauth_org_not_allowed", "account_on_hold", "billing_error", "model_not_found"}:
            errors.append("Claude authentication, billing or model-route retry was observed")
    for message in parsed["assistant_messages"]:
        if message.get("error") in {"authentication_failed", "billing_error", "model_not_found", "invalid_request"}:
            errors.append("Claude reported an infrastructure API error")
    status = (parsed["result"] or {}).get("api_error_status")
    if type(status) is int and status >= 400:
        errors.append(f"Claude final result reported infrastructure API status {status}")
    return errors


async def _case(
    session: ClientSession, task: dict[str, Any], repeat: int, arm: str, index: int,
    settings: dict[str, Any], skill: _Skill, skill_bytes: bytes, baseline: dict[str, Any],
    output: Path, ownership: str, trace: TextIO,
) -> dict[str, Any]:
    row = _row(task, repeat, arm)
    identity = {"id": task["id"], "repeat": repeat, "arm": arm}
    case_root = output / "cases" / f"{index:04d}"
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True)
    if arm == "skill_mcp":
        copied = workspace / ".claude" / "skills" / skill.name / "SKILL.md"
        copied.parent.mkdir(parents=True)
        copied.write_bytes(skill_bytes)
        if _hash(copied.read_bytes()) != skill.sha256:
            raise RuntimeError("Claude mounted skill bytes do not match the frozen skill")
    stdout, stderr = case_root / "stdout.jsonl", case_root / "stderr.log"
    name = f"codalith-claude-{ownership[:12]}-{index:04d}"
    command = _container_command(
        image=settings["image"], model=settings["model"], effort=settings["effort"], endpoint=settings["endpoint"],
        mcp_container=settings["mcp_container"], settings_file=Path(settings["settings_file"]),
        workspace=workspace, container_name=name, ownership=ownership,
    )
    row.update({"started_at_utc": _utc(), "stdout_path": str(stdout), "stderr_path": str(stderr), "container_name": name})
    _write(trace, {"event": "case_start", **identity, "prompt": task["prompt"], "workspace": str(workspace), "container_name": name})
    started = time.perf_counter()
    errors: list[str] = []
    try:
        row["process"] = await anyio.to_thread.run_sync(functools.partial(
            _run_container, command, task["prompt"], stdout, stderr, settings["case_timeout_s"], name, ownership,
        ))
        row["wall_ms"] = row["process"]["wall_ms"]
        events, stream_errors = _events(stdout, trace, identity)
        parsed = parse_claude_events(events)
        row["claude_events"] = parsed
        for field in ("usage", "usage_raw", "model_usage", "raw_final_content", "duration_ms", "duration_api_ms", "total_cost_usd"):
            row[field] = parsed[field]
        row["claude_num_turns"] = parsed["num_turns"]
        _observe(row, parsed, skill, skill_bytes)
        errors.extend(stream_errors + parsed["parse_errors"])
        runtime_errors = _runtime_errors(parsed, settings["model"], [skill.name] if arm == "skill_mcp" else [])
        errors.extend(runtime_errors)
        row["infrastructure_failure"] = bool(runtime_errors)
        if row["process"]["timed_out"]:
            errors.append("Claude case exceeded its process timeout")
        if row["process"]["exit_code"] != 0:
            errors.append(f"Claude process exited with code {row['process']['exit_code']}")
        if not parsed["completed"]:
            errors.append("Claude event stream did not complete successfully with a final answer")
        if isinstance(parsed["structured_output"], dict):
            try:
                row["final"] = _parse_final(row["raw_final_content"])
            except (ValueError, TypeError) as exc:
                errors.append(f"Final output contract failed: {exc}")
        else:
            errors.append("Claude structured_output contract was not returned")
    except Exception as exc:
        errors.append(_exception_text(exc))
        row["infrastructure_failure"] = True
    row["wall_ms"] = row["wall_ms"] if row["wall_ms"] is not None else (time.perf_counter() - started) * 1000
    row["finished_at_utc"] = _utc()
    row["error"] = "; ".join(errors) if errors else None
    if row["final"] is not None:
        audit_started = time.perf_counter()
        row["citation_audit"] = await _audit_citations(session, row["final"]["citations"], row["issued_sources"], baseline, trace, identity)
        row["audit_wall_ms"] = (time.perf_counter() - audit_started) * 1000
    row["evidence_coverage"] = _coverage(task, row["citation_audit"])
    return row


def _summaries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for arm in _ARMS:
        group = [row for row in rows if row["arm"] == arm]
        walls = [row["wall_ms"] for row in group if row["wall_ms"] is not None]
        citations = [item for row in group for item in row["citation_audit"]]
        summary: dict[str, Any] = {
            "planned_cases": len(group), "attempted_cases": sum(not row["not_run"] for row in group),
            "failed_cases": sum(row["error"] is not None for row in group), "not_run_cases": sum(row["not_run"] for row in group),
            "final_json_cases": sum(row["final"] is not None for row in group),
            "mcp_calls": sum(row["mcp_calls"] for row in group), "unknown_calls": sum(row["unknown_calls"] for row in group),
            "read_calls": sum(row["read_calls"] for row in group), "skill_calls": sum(row["skill_calls"] for row in group),
            "tool_errors": sum(row["tool_errors"] for row in group),
            "skill_activated_cases": sum(row["skill_activation"] == "observed" for row in group),
            "skill_partial_cases": sum(row["skill_activation"] == "partial_observed" for row in group),
            "policy_violation_cases": sum(bool(row["native_policy_violations"]) for row in group),
            "source_forbidden_violations": sum(row["mcp_calls"] > 0 for row in group if row["expected_tool_behavior"].get("source_query") == "forbidden"),
            "citation_count": len(citations), "valid_citations": sum(item["valid"] for item in citations),
            "citation_validity": sum(item["valid"] for item in citations) / len(citations) if citations else None,
            "mean_wall_ms_attempted": sum(walls) / len(walls) if walls else None,
            "audit_wall_ms": sum(row["audit_wall_ms"] for row in group), "model_wall_ms": None,
        }
        for field in _USAGE_FIELDS:
            values = [row["usage"].get(field) for row in group]
            known = [value for value in values if type(value) is int]
            summary[field + "_known_sum"] = sum(known) if known else None
            summary[field + "_missing_cases"] = sum(value is None for value in values)
        summary["token_usage_complete"] = bool(group) and all(row["usage"].get("total_tokens") is not None for row in group)
        summary["reported_total_tokens"] = summary["total_tokens_known_sum"] if summary["token_usage_complete"] else None
        summaries[arm] = summary
    return summaries


async def _run(
    settings: dict[str, Any], tasks: list[dict[str, Any]], dataset: Path,
    skill: _Skill, output: Path, dataset_sha256: str,
) -> dict[str, Any]:
    schedule = _schedule(tasks, settings["repeats"])
    ownership = uuid.uuid4().hex
    skill_bytes = skill.path.read_bytes()
    provider = Path(settings["settings_file"])
    provider_stat = _provider_stat(provider)
    metadata: dict[str, Any] = {
        "started_at_utc": _utc(), "runner": "native_claude_docker", "ownership": ownership,
        "settings": {key: value for key, value in settings.items() if key != "settings_file"},
        "dataset_sha256": dataset_sha256, "skill": {"name": skill.name, "description": skill.description, "sha256": skill.sha256},
        "final_schema": _FINAL_SCHEMA, "final_schema_sha256": _hash(_FINAL_SCHEMA),
        "base_prompt_sha256": _hash(_BASE_SYSTEM), "append_system_prompt_sha256": _hash(_CLAUDE_SYSTEM),
        "native_config": claude_config_overrides(),
        "provider_settings_stat": provider_stat,
        "schedule": [{"id": task["id"], "repeat": repeat, "arm": arm} for task, repeat, arm in schedule],
        "usage_semantics": "Latest result.usage is main-loop uncached input + cache-read + cache-creation + output; modelUsage is separate whole-tree cumulative data and is not added to it. Missing counters stay null; assistant output counts are placeholders.",
        "timeout_semantics": "One hard process timeout per case including launch, excluding citation audit. Per-task timeout or output-contract failure remains a failed attempted row and does not stop the schedule; evidenced authentication/model-route/MCP startup or frozen-input failure stops later cases, which remain not-run rows.",
    }
    rows: list[dict[str, Any]] = []
    infrastructure_errors: list[str] = []
    with (output / "trace.jsonl").open("x", encoding="utf-8") as trace:
        _write(trace, {"event": "run_start", "metadata": metadata})
        try:
            _frozen(dataset, dataset_sha256, skill)
            _frozen_provider(provider, provider_stat)
            async with streamable_http_client(settings["endpoint"]) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    native = (await session.list_tools()).tools
                    if {item.name for item in native} != _MCP_TOOLS:
                        raise RuntimeError("Claude endpoint must expose the registered Codalith MCP tool contract")
                    baseline = await _snapshot(session)
                    metadata.update({"mcp_initialize": initialized.model_dump(mode="json"), "mcp_start": baseline, "native_tools_sha256": _hash([item.model_dump(mode="json") for item in native])})
                    _write(trace, {"event": "initialized", "metadata": metadata})
                    for index, (task, repeat, arm) in enumerate(schedule):
                        _frozen(dataset, dataset_sha256, skill)
                        _frozen_provider(provider, provider_stat)
                        if await _snapshot(session) != baseline:
                            raise RuntimeError("MCP generation changed before Claude task")
                        row = await _case(session, task, repeat, arm, index, settings, skill, skill_bytes, baseline, output, ownership, trace)
                        rows.append(row)
                        try:
                            _frozen(dataset, dataset_sha256, skill)
                            _frozen_provider(provider, provider_stat)
                            if await _snapshot(session) != baseline:
                                raise RuntimeError("MCP generation changed after Claude task")
                        except Exception as exc:
                            row["error"] = "; ".join(value for value in (row["error"], _exception_text(exc)) if value)
                            row["infrastructure_failure"] = True
                        _write(trace, {"event": "case_complete", "row": row})
                        if row["infrastructure_failure"]:
                            raise RuntimeError(row["error"])
                        cleanup = (row.get("process") or {}).get("cleanup", {})
                        if cleanup.get("status") not in {"removed", "not_present"}:
                            raise RuntimeError("Claude owned-container cleanup was not established")
                    metadata["mcp_end"] = await _snapshot(session)
        except Exception as exc:
            infrastructure_errors.append(_exception_text(exc))
            _write(trace, {"event": "run_error", "error": infrastructure_errors[-1]})
        attempted = len(rows)
        for task, repeat, arm in schedule[attempted:]:
            row = _row(task, repeat, arm)
            row.update({"error": "Not run after Claude infrastructure failure", "not_run": True})
            row["evidence_coverage"] = _coverage(task, [])
            rows.append(row)
            _write(trace, {"event": "case_not_run", "row": row})
        metadata["finished_at_utc"] = _utc()
        report = {
            "completed": attempted == len(schedule) and not infrastructure_errors,
            "errors": sum(row["error"] is not None for row in rows) + len(infrastructure_errors),
            "infrastructure_errors": infrastructure_errors, "metadata": metadata,
            "summaries": _summaries(rows), "rows": rows,
            "interpretation": "Native completion and valid MCP citations do not establish semantic correctness; blind review and native-policy/tool-use overlays remain required. Reported model names do not independently verify gateway weights, and cost is a CLI estimate.",
        }
        _write(trace, {"event": "run_complete", "completed": report["completed"], "errors": report["errors"], "summaries": report["summaries"]})
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_claude_agent_comparison(
    *, image: str, model: str, effort: str, endpoint: str, mcp_container: str,
    settings_file: str | Path, dataset_path: str | Path, skill_path: str | Path,
    output_dir: str | Path, repeats: int = 2, case_timeout_s: float = 600,
) -> dict[str, Any]:
    """Compare native Claude sessions without reading provider settings on the host."""
    if repeats < 1 or case_timeout_s <= 0:
        raise ValueError("Claude repeats and case timeout must be positive")
    if not all(isinstance(value, str) and value for value in (image, model, effort, endpoint, mcp_container)):
        raise ValueError("Claude image, model, effort, endpoint and MCP container are required")
    if model.lower() not in _MODEL_NAMES:
        raise ValueError("Claude comparison requires the frozen GLM-5.3 route")
    provider = Path(settings_file).resolve()
    if not provider.is_file():
        raise ValueError("Claude settings_file must be an existing file")
    dataset = Path(dataset_path).resolve()
    dataset_sha256 = _hash(dataset.read_bytes())
    tasks = _load_tasks(dataset)
    skill = _load_skill(Path(skill_path).resolve())
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", skill.name) is None:
        raise ValueError("Claude skill name must be a safe directory name")
    settings = {
        "image": image, "model": model, "effort": effort, "endpoint": endpoint,
        "mcp_container": mcp_container, "settings_file": str(provider),
        "repeats": repeats, "case_timeout_s": case_timeout_s,
    }
    return anyio.run(_run, settings, tasks, dataset, skill, _output_path(Path(output_dir)), dataset_sha256)
