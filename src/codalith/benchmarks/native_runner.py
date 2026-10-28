"""Run isolated native Codex comparisons and audit their public MCP evidence."""

from __future__ import annotations

import functools
import json
import re
import shlex
import subprocess
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
from codalith.benchmarks.native_events import (
    normalize_native_mcp_result,
    parse_native_events,
)

_OWNERSHIP_LABEL = "org.codalith.native-study"
_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
_FORBIDDEN_ITEMS = frozenset({"web_search", "web_search_call", "file_change", "browser"})
_NATIVE_DEVELOPER = (
    _BASE_SYSTEM + " Source evidence must come only from Codalith MCP. "
    "You may read a discovered skill. Do not read authentication files or other runtime "
    "materials, and do not use other network sources to obtain evidence."
)


def _exception_text(exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, BaseExceptionGroup):
        return message + " [" + "; ".join(_exception_text(child) for child in exc.exceptions) + "]"
    return message


def native_config_overrides(endpoint: str) -> dict[str, Any]:
    """Keep product controls in one place for the frozen native preflight."""
    return {
        "mcp_servers.codalith.url": endpoint,
        "mcp_servers.codalith.enabled": True,
        "mcp_servers.codalith.required": True,
        "mcp_servers.codalith.enabled_tools": sorted(_MCP_TOOLS),
        "web_search": "disabled",
        "approval_policy": "never",
        "features.apps": False,
        "features.plugins": False,
        "features.remote_plugin": False,
        "features.memories": False,
        "features.multi_agent": False,
        "project_doc_max_bytes": 0,
        "developer_instructions": _NATIVE_DEVELOPER,
        "skills.config": [
            {"path": f"/codex-home/skills/.system/{name}/SKILL.md", "enabled": False}
            for name in ("imagegen", "openai-docs", "skill-creator", "skill-installer")
        ],
    }


def _toml_value(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(key)}={_toml_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_toml_value(item) for item in value) + "]"
    if value is None:
        raise ValueError("Native config overrides cannot contain null")
    return json.dumps(value, ensure_ascii=True)


def _mount(path: Path, destination: str) -> str:
    resolved = str(path.resolve())
    if "," in resolved or "\n" in resolved or "\r" in resolved:
        raise ValueError("Docker bind paths must not contain commas or line breaks")
    return f"type=bind,source={resolved},target={destination},readonly"


def _container_command(
    *, image: str, model: str, reasoning_effort: str, endpoint: str,
    mcp_container: str, auth_file: Path, workspace: Path, schema: Path,
    container_name: str, ownership: str, sandbox: str,
) -> list[str]:
    command = [
        "docker", "run", "--interactive", "--pull", "never", "--name", container_name,
        "--label", f"{_OWNERSHIP_LABEL}={ownership}", "--rm", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev", "--tmpfs", "/codex-home:rw,nosuid,nodev",
        "--network", f"container:{mcp_container}",
        "--env", "CODEX_HOME=/codex-home", "--env", "HOME=/tmp/home",
        "--env", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE=Codex Desktop",
        "--mount", _mount(workspace, "/workspace"),
        "--mount", _mount(schema, "/schema.json"),
        "--mount", _mount(auth_file, "/codex-home/auth.json"),
        image,
        "exec", "--ignore-user-config", "--ephemeral", "--json",
        "--skip-git-repo-check", "--output-schema", "/schema.json",
        "--model", model, "--cd", "/workspace", "--sandbox", sandbox,
        "-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}",
    ]
    for key, value in native_config_overrides(endpoint).items():
        command.extend(["-c", f"{key}={_toml_value(value)}"])
    return [*command, "-"]


def _cleanup_container(container_name: str, ownership: str) -> dict[str, Any]:
    inspected = subprocess.run(
        ["docker", "container", "inspect", container_name],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    if inspected.returncode != 0:
        if re.search(r"\bno such (?:container|object)\b", inspected.stderr, re.IGNORECASE):
            return {"status": "not_present"}
        return {"status": "inspect_failed", "exit_code": inspected.returncode}
    try:
        documents = json.loads(inspected.stdout)
        if not isinstance(documents, list) or len(documents) != 1:
            return {"status": "refused", "reason": "Unexpected inspection result"}
        document = documents[0]
        identifier = document.get("Id")
        if (
            document.get("Name") != "/" + container_name
            or document.get("Config", {}).get("Labels", {}).get(_OWNERSHIP_LABEL) != ownership
            or not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
        ):
            return {"status": "refused", "reason": "Container name or ownership label mismatch"}
    except (ValueError, AttributeError, TypeError):
        return {"status": "refused", "reason": "Malformed inspection result"}
    removed = subprocess.run(
        ["docker", "rm", "--force", identifier],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    return {"status": "removed" if removed.returncode == 0 else "remove_failed", "exit_code": removed.returncode}


def _run_container(
    command: list[str], prompt: str, stdout_path: Path, stderr_path: Path,
    timeout_s: float, container_name: str, ownership: str,
) -> dict[str, Any]:
    cleanup: dict[str, Any] | None = None
    timed_out = False
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        started = time.perf_counter()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        try:
            process.communicate(prompt.encode("utf-8"), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                cleanup = _cleanup_container(container_name, ownership)
            except Exception as exc:
                cleanup = {"status": "cleanup_error", "error": _exception_text(exc)}
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
        wall_ms = (time.perf_counter() - started) * 1000
    if cleanup is None:
        try:
            cleanup = _cleanup_container(container_name, ownership)
        except Exception as exc:
            cleanup = {"status": "cleanup_error", "error": _exception_text(exc)}
    return {"exit_code": process.returncode, "timed_out": timed_out, "wall_ms": wall_ms, "cleanup": cleanup}


def _row(task: dict[str, Any], repeat: int, arm: str) -> dict[str, Any]:
    return {
        "id": task["id"], "category": task.get("category"), "repeat": repeat, "arm": arm,
        "requires_source": task.get("requires_source", False),
        "expected_target": task.get("expected_target"), "expected_facts": task.get("expected_facts", []),
        "expected_abstention": task.get("expected_abstention", {}),
        "expected_tool_behavior": task.get("expected_tool_behavior", {}),
        "started_at_utc": None, "finished_at_utc": None, "wall_ms": None,
        "model_wall_ms": None, "mcp_wall_ms": None, "skill_wall_ms": None, "audit_wall_ms": 0.0,
        "outer_turns_started": 0, "outer_turns_completed": 0, "outer_turns_failed": 0,
        "mcp_calls": 0, "status_calls": 0, "source_calls": 0, "unknown_calls": 0, "tool_errors": 0,
        "skill_reads": 0, "skill_read_successes": 0, "skill_activation": "not_observed",
        "usage": dict.fromkeys(_USAGE_FIELDS), "raw_final_content": "", "final": None,
        "citation_audit": [], "evidence_coverage": [], "issued_sources": {},
        "native_policy_violations": [], "native_command_review": [], "native_events": None, "process": None,
        "error": None, "not_run": False,
    }


def _frozen(dataset: Path, dataset_sha256: str, skill: _Skill) -> None:
    if _hash(dataset.read_bytes()) != dataset_sha256:
        raise RuntimeError("Frozen native task dataset changed")
    skill.read()


def _events(path: Path, trace: TextIO, identity: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Native event must be an object")
            except ValueError as exc:
                errors.append(f"Native stdout line {number}: {exc}")
                _write(trace, {"event": "native_output_error", **identity, "line": number, "raw": line, "error": str(exc)})
                continue
            events.append(value)
            _write(trace, {"event": "native_event", **identity, "native_event": value})
    return events, errors


def _observe(
    row: dict[str, Any], parsed: dict[str, Any], skill: _Skill, skill_bytes: bytes,
) -> None:
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
        payload = normalize_native_mcp_result(item)
        if payload is None:
            row["tool_errors"] += 1
        elif known:
            _issued_sources(payload, issued)
    skill_path = f".agents/skills/{skill.name}/SKILL.md"
    normalized_body = skill_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    for item in parsed["command_items"]:
        command = item.get("command", "")
        output = item.get("aggregated_output", "")
        candidate = isinstance(command, str) and skill_path in command
        if candidate:
            row["skill_reads"] += 1
        successful = (
            row["arm"] == "skill_mcp" and candidate
            and item.get("status") == "completed" and item.get("exit_code") == 0
            and isinstance(output, str)
        )
        normalized_output = output.replace("\r\n", "\n").replace("\r", "\n") if isinstance(output, str) else ""
        confirmed = successful and normalized_body in normalized_output
        partial = successful and bool(normalized_output.strip()) and normalized_output.strip() in normalized_body
        if confirmed:
            row["skill_read_successes"] += 1
            row["skill_activation"] = "observed"
        elif partial and row["skill_activation"] != "observed":
            row["skill_activation"] = "partial_observed"
        single_read = isinstance(command, str) and _skill_read_only(command, skill_path)
        classification = "single_skill_read" if single_read else "metadata" if isinstance(command, str) and command.strip() in {"pwd", "ls", "ls -la", "ls /workspace", "ls -la /workspace"} else "review_required"
        row["native_command_review"].append({
            "classification": classification,
            "skill_body_evidence": "full" if confirmed else "partial" if partial else "not_observed",
            "item": item,
        })
    for item in parsed["other_items"]:
        if item.get("type") in _FORBIDDEN_ITEMS:
            violations.append({"kind": "forbidden_native_item", "item": item})
    row["issued_sources"] = issued


def _skill_read_only(command: str, skill_path: str) -> bool:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if len(arguments) == 3 and Path(arguments[0]).name in {"bash", "sh"} and arguments[1] in {"-c", "-lc"}:
        return _skill_read_only(arguments[2], skill_path)
    paths = {skill_path, "./" + skill_path, "/workspace/" + skill_path}
    if len(arguments) == 2 and arguments[0] == "cat" and arguments[1] in paths:
        return True
    if len(arguments) == 3 and arguments[:2] == ["cat", "--"] and arguments[2] in paths:
        return True
    if len(arguments) == 4 and arguments[:2] == ["sed", "-n"] and arguments[3] in paths:
        return re.fullmatch(r"[0-9]+(?:,[0-9]+)?p", arguments[2]) is not None
    if len(arguments) == 4 and arguments[:2] == ["head", "-n"] and arguments[3] in paths:
        return arguments[2].isdigit()
    return False


async def _case(
    session: ClientSession, task: dict[str, Any], repeat: int, arm: str, index: int,
    settings: dict[str, Any], skill: _Skill, skill_bytes: bytes, schema: Path,
    baseline: dict[str, Any], output: Path, ownership: str, trace: TextIO,
) -> dict[str, Any]:
    row = _row(task, repeat, arm)
    identity = {"id": task["id"], "repeat": repeat, "arm": arm}
    case_root = output / "cases" / f"{index:04d}"
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True)
    if arm == "skill_mcp":
        copied = workspace / ".agents" / "skills" / skill.name / "SKILL.md"
        copied.parent.mkdir(parents=True)
        copied.write_bytes(skill_bytes)
        if _hash(copied.read_bytes()) != skill.sha256:
            raise RuntimeError("Native mounted skill bytes do not match the frozen skill")
    stdout = case_root / "stdout.jsonl"
    stderr = case_root / "stderr.log"
    container_name = f"codalith-native-{ownership[:12]}-{index:04d}"
    command = _container_command(
        image=settings["image"], model=settings["model"], reasoning_effort=settings["reasoning_effort"],
        endpoint=settings["endpoint"], mcp_container=settings["mcp_container"],
        auth_file=Path(settings["auth_file"]), workspace=workspace, schema=schema,
        container_name=container_name, ownership=ownership, sandbox=settings["sandbox"],
    )
    prompt = task["prompt"]
    row.update({"started_at_utc": _utc(), "stdout_path": str(stdout), "stderr_path": str(stderr), "container_name": container_name})
    _write(trace, {"event": "case_start", **identity, "prompt": prompt, "workspace": str(workspace), "container_name": container_name})
    started = time.perf_counter()
    errors: list[str] = []
    try:
        row["process"] = await anyio.to_thread.run_sync(functools.partial(
            _run_container, command, prompt, stdout, stderr, settings["case_timeout_s"],
            container_name, ownership,
        ))
        row["wall_ms"] = row["process"]["wall_ms"]
        events, stream_errors = _events(stdout, trace, identity)
        parsed = parse_native_events(events)
        row["native_events"] = parsed
        row["raw_final_content"] = parsed["raw_final_content"]
        row["usage"] = parsed["usage"]
        for key in ("outer_turns_started", "outer_turns_completed", "outer_turns_failed"):
            row[key] = parsed[key]
        _observe(row, parsed, skill, skill_bytes)
        errors.extend(stream_errors)
        errors.extend(parsed["parse_errors"])
        if row["process"]["timed_out"]:
            errors.append("Native case exceeded its process timeout")
        if row["process"]["exit_code"] != 0:
            errors.append(f"Native Codex process exited with code {row['process']['exit_code']}")
        if not parsed["completed"]:
            errors.append("Native event stream did not complete successfully with a final answer")
        if row["raw_final_content"]:
            try:
                row["final"] = _parse_final(row["raw_final_content"])
            except (ValueError, TypeError) as exc:
                errors.append(f"Final output contract failed: {exc}")
    except Exception as exc:
        errors.append(_exception_text(exc))
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
        wall = [row["wall_ms"] for row in group if row["wall_ms"] is not None]
        evidence_count = sum(bool(fact.get("evidence")) for row in group for fact in row["expected_facts"])
        covered = sum(fact["covered"] is True for row in group for fact in row["evidence_coverage"])
        citations = [citation for row in group for citation in row["citation_audit"]]
        summary: dict[str, Any] = {
            "planned_cases": len(group), "attempted_cases": sum(not row["not_run"] for row in group),
            "failed_cases": sum(row["error"] is not None for row in group),
            "not_run_cases": sum(row["not_run"] for row in group),
            "final_json_cases": sum(row["final"] is not None for row in group),
            "mcp_calls": sum(row["mcp_calls"] for row in group),
            "unknown_calls": sum(row["unknown_calls"] for row in group),
            "tool_errors": sum(row["tool_errors"] for row in group),
            "policy_violation_cases": sum(bool(row["native_policy_violations"]) for row in group),
            "skill_activated_cases": sum(row["skill_activation"] == "observed" for row in group),
            "skill_partial_cases": sum(row["skill_activation"] == "partial_observed" for row in group),
            "command_review_required_cases": sum(any(item["classification"] == "review_required" for item in row["native_command_review"]) for row in group),
            "source_forbidden_violations": sum(row["mcp_calls"] > 0 for row in group if row["expected_tool_behavior"].get("source_query") == "forbidden"),
            "expected_evidence_facts": evidence_count, "covered_facts": covered,
            "evidence_coverage": covered / evidence_count if evidence_count else None,
            "citation_count": len(citations), "valid_citations": sum(item["valid"] for item in citations),
            "citation_validity": sum(item["valid"] for item in citations) / len(citations) if citations else None,
            "mean_wall_ms_attempted": sum(wall) / len(wall) if wall else None,
            "audit_wall_ms": sum(row["audit_wall_ms"] for row in group),
            "model_wall_ms": None,
        }
        for field in _USAGE_FIELDS:
            values = [row["usage"].get(field) for row in group]
            known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
            summary[field + "_known_sum"] = sum(known) if known else None
            summary[field + "_missing_cases"] = sum(value is None for value in values)
        complete = bool(group) and all(
            isinstance(row["usage"].get(field), int) and not isinstance(row["usage"].get(field), bool)
            for row in group for field in ("input_tokens", "output_tokens")
        )
        summary["token_usage_complete"] = complete
        summary["reported_total_tokens"] = summary["input_tokens_known_sum"] + summary["output_tokens_known_sum"] if complete else None
        summaries[arm] = summary
    return summaries


async def _run(
    settings: dict[str, Any], tasks: list[dict[str, Any]], dataset: Path,
    skill: _Skill, output: Path, dataset_sha256: str,
) -> dict[str, Any]:
    schedule = _schedule(tasks, settings["repeats"])
    ownership = uuid.uuid4().hex
    schema = output / "schema.json"
    with schema.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(_FINAL_SCHEMA, ensure_ascii=False, indent=2) + "\n")
    skill_bytes = skill.path.read_bytes()
    metadata: dict[str, Any] = {
        "started_at_utc": _utc(), "runner": "native_codex_docker", "ownership": ownership,
        "settings": {key: value for key, value in settings.items() if key != "auth_file"},
        "dataset_sha256": dataset_sha256,
        "skill": {"name": skill.name, "description": skill.description, "sha256": skill.sha256},
        "final_schema": _FINAL_SCHEMA, "final_schema_sha256": _hash(_FINAL_SCHEMA),
        "base_prompt_sha256": _hash(_BASE_SYSTEM),
        "developer_instructions_sha256": _hash(_NATIVE_DEVELOPER),
        "native_config": native_config_overrides(settings["endpoint"]),
        "schedule": [{"id": task["id"], "repeat": repeat, "arm": arm} for task, repeat, arm in schedule],
        "usage_semantics": "Latest native turn.completed usage, input/cached/output/reasoning preserved; cached is an input subset; missing stays null.",
        "timeout_semantics": "One hard process timeout per case; launch cost included; owned-container cleanup follows timeout; citation audit excluded.",
    }
    rows: list[dict[str, Any]] = []
    infrastructure_errors: list[str] = []
    with (output / "trace.jsonl").open("x", encoding="utf-8") as trace:
        _write(trace, {"event": "run_start", "metadata": metadata})
        try:
            _frozen(dataset, dataset_sha256, skill)
            async with streamable_http_client(settings["endpoint"]) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    native = (await session.list_tools()).tools
                    if {item.name for item in native} != _MCP_TOOLS:
                        raise RuntimeError("Native endpoint must expose the registered Codalith MCP tool contract")
                    baseline = await _snapshot(session)
                    metadata.update({"mcp_initialize": initialized.model_dump(mode="json"), "mcp_start": baseline, "native_tools_sha256": _hash([item.model_dump(mode="json") for item in native])})
                    _write(trace, {"event": "initialized", "metadata": metadata})
                    for index, (task, repeat, arm) in enumerate(schedule):
                        _frozen(dataset, dataset_sha256, skill)
                        if await _snapshot(session) != baseline:
                            raise RuntimeError("MCP generation changed before native task")
                        row = await _case(session, task, repeat, arm, index, settings, skill, skill_bytes, schema, baseline, output, ownership, trace)
                        rows.append(row)
                        _frozen(dataset, dataset_sha256, skill)
                        if await _snapshot(session) != baseline:
                            row["error"] = "MCP generation changed after native task"
                            _write(trace, {"event": "case_complete", "row": row})
                            raise RuntimeError(row["error"])
                        _write(trace, {"event": "case_complete", "row": row})
                        cleanup = (row.get("process") or {}).get("cleanup", {})
                        if cleanup.get("status") not in {"removed", "not_present"}:
                            raise RuntimeError("Native owned-container cleanup was not established")
                    metadata["mcp_end"] = await _snapshot(session)
        except Exception as exc:
            infrastructure_errors.append(_exception_text(exc))
            _write(trace, {"event": "run_error", "error": infrastructure_errors[-1]})
        attempted = len(rows)
        for task, repeat, arm in schedule[attempted:]:
            row = _row(task, repeat, arm)
            row.update({"error": "Not run after native infrastructure failure", "not_run": True})
            row["evidence_coverage"] = _coverage(task, [])
            rows.append(row)
            _write(trace, {"event": "case_not_run", "row": row})
        metadata["finished_at_utc"] = _utc()
        report = {
            "completed": attempted == len(schedule) and not infrastructure_errors,
            "errors": sum(row["error"] is not None for row in rows) + len(infrastructure_errors),
            "infrastructure_errors": infrastructure_errors, "metadata": metadata,
            "summaries": _summaries(rows), "rows": rows,
            "interpretation": "Native process completion and citation validity are not semantic correctness. Blind review and native policy/tool-use overlays remain required.",
        }
        _write(trace, {"event": "run_complete", "completed": report["completed"], "errors": report["errors"], "summaries": report["summaries"]})
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_native_agent_comparison(
    *, image: str, model: str, reasoning_effort: str, endpoint: str, mcp_container: str,
    auth_file: str | Path, dataset_path: str | Path, skill_path: str | Path,
    output_dir: str | Path, repeats: int = 2, case_timeout_s: float = 600,
    sandbox: str = "read-only",
) -> dict[str, Any]:
    """Compare native agents in disposable containers without exposing qrels or source."""
    if repeats < 1 or case_timeout_s <= 0:
        raise ValueError("Native repeats and case timeout must be positive")
    if sandbox not in {"read-only", "danger-full-access"}:
        raise ValueError("Native sandbox must be an explicitly selected supported profile")
    if not all(isinstance(value, str) and value for value in (image, model, reasoning_effort, endpoint, mcp_container)):
        raise ValueError("Native image, model, reasoning effort, endpoint and MCP container are required")
    auth = Path(auth_file).resolve()
    if not auth.is_file():
        raise ValueError("Native auth_file must be an existing file")
    dataset = Path(dataset_path).resolve()
    dataset_sha256 = _hash(dataset.read_bytes())
    tasks = _load_tasks(dataset)
    skill = _load_skill(Path(skill_path).resolve())
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", skill.name) is None:
        raise ValueError("Native skill name must be a safe directory name")
    settings = {
        "image": image, "model": model, "reasoning_effort": reasoning_effort,
        "endpoint": endpoint, "mcp_container": mcp_container, "auth_file": str(auth),
        "repeats": repeats, "case_timeout_s": case_timeout_s, "sandbox": sandbox,
    }
    output = _output_path(Path(output_dir))
    return anyio.run(_run, settings, tasks, dataset, skill, output, dataset_sha256)
