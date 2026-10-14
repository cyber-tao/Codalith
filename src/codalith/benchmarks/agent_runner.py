"""Reproducible, local-model agent comparisons over the public MCP transport."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from codalith.corpus.uris import parse_uri
from codalith.errors import URIResolutionError
from codalith.query.models import ReadResponse

_ARMS = ("mcp_only", "skill_mcp")
_MCP_TOOLS = frozenset(
    f"codalith_{name}" for name in ("search", "context", "read", "symbol", "graph", "compare", "status")
)
_KEEP_ALIVE = "30m"
_BASE_SYSTEM = (
    "Answer the user's task. Use the available tools when source evidence is needed. "
    "Ground source claims in the available corpus and its stated version, and cite only "
    "source URIs actually returned by tools. State evidence limits and abstain when the "
    "available evidence cannot support an answer. Do not inspect local files except through "
    "an explicitly provided tool. Return only a JSON object with exactly these keys: "
    '"answer" (string), "citations" (array of objects with exactly string "uri" and '
    'string "claim"), and "abstained" (boolean). Answer in the user\'s language.'
)
_FINAL_SCHEMA = {
    "type": "object",
    "required": ["answer", "citations", "abstained"],
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "abstained": {"type": "boolean"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["uri", "claim"],
                "additionalProperties": False,
                "properties": {"uri": {"type": "string"}, "claim": {"type": "string"}},
            },
        },
    },
}


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: bytes | str | object) -> str:
    data = value if isinstance(value, bytes) else (value if isinstance(value, str) else _json(value)).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class _ModelError(RuntimeError):
    def __init__(self, message: str, response: object = None) -> None:
        super().__init__(message)
        self.response = response


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise _ModelError("Model API redirects are forbidden")


def _local_model_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("model_url must be a loopback HTTP origin without credentials or a path")
    host = parsed.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if host.lower() != "localhost":
            raise ValueError("model_url must use a loopback IP address or localhost") from None
        addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port)]
    if not addresses or not all(address.is_loopback for address in addresses):
        raise ValueError("model_url must resolve exclusively to loopback addresses")
    return value.rstrip("/")


def _api_request(
    origin: str, route: str, payload: dict[str, Any] | None, timeout_s: float
) -> dict[str, Any]:
    request = Request(
        origin + route,
        data=None if payload is None else _json(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: object = json.loads(raw)
        except ValueError:
            body = raw
        raise _ModelError(f"Model API HTTP {exc.code}: {raw}", body) from exc
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise _ModelError("Model API returned non-JSON content", raw) from exc
    if not isinstance(result, dict):
        raise _ModelError("Model API returned a non-object JSON value", result)
    if result.get("error"):
        raise _ModelError(f"Model API error: {result['error']}", result)
    return result


@dataclass(frozen=True)
class _Settings:
    endpoint: str
    model_url: str
    model: str
    repeats: int
    max_turns: int
    max_mcp_calls: int
    context_tokens: int
    output_tokens: int
    timeout_s: float
    seed: int
    think: bool


@dataclass(frozen=True)
class _Skill:
    path: Path
    name: str
    description: str
    sha256: str

    def read(self) -> str:
        raw = self.path.read_bytes()
        if _hash(raw) != self.sha256:
            raise RuntimeError("Frozen skill changed during the comparison")
        return raw.decode("utf-8")

    @property
    def tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_skill",
                "description": "Read the named available skill's instructions when useful for the task.",
                "parameters": {
                    "type": "object",
                    "required": ["name"],
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string", "enum": [self.name]}},
                },
            },
        }


def _load_skill(path: Path) -> _Skill:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if match is None:
        raise ValueError("Skill requires YAML frontmatter")
    fields = dict(re.findall(r"^(name|description):\s*(.+)$", match[1], re.MULTILINE))
    if set(fields) != {"name", "description"}:
        raise ValueError("Skill name and single-line description are required")
    return _Skill(path, fields["name"].strip(), fields["description"].strip(), _hash(raw))


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        task = json.loads(line)
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not isinstance(task.get("prompt"), str):
            raise ValueError(f"Invalid agent task at line {line_number}")
        if task["id"] in seen:
            raise ValueError(f"Duplicate agent task id: {task['id']}")
        if not isinstance(task.get("expected_facts", []), list):
            raise ValueError(f"Invalid expected_facts: {task['id']}")
        behavior = task.get("expected_tool_behavior", {}).get("source_query", "optional")
        if behavior not in {"required", "optional", "forbidden"}:
            raise ValueError(f"Invalid source_query: {task['id']}")
        seen.add(task["id"])
        tasks.append(task)
    if not tasks:
        raise ValueError("Agent task dataset is empty")
    return tasks


def _output_path(path: Path) -> Path:
    path = path.resolve()
    root = Path(__file__).resolve().parents[3]
    if path.is_relative_to(root):
        relative = path.relative_to(root).as_posix() + "/"
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative], cwd=root, check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise ValueError("Agent traces inside the repository require a git-ignored output_dir")
    path.mkdir(parents=True, exist_ok=True)
    if (path / "trace.jsonl").exists() or (path / "report.json").exists():
        raise ValueError("output_dir already contains an agent run")
    return path


def _write(stream: TextIO, event: dict[str, Any]) -> None:
    stream.write(_json({"timestamp_utc": _utc(), **event}) + "\n")
    stream.flush()


def run_agent_comparison(
    *,
    endpoint: str,
    model_url: str,
    model: str,
    dataset_path: str | Path,
    skill_path: str | Path,
    output_dir: str | Path,
    repeats: int = 2,
    max_turns: int = 8,
    max_mcp_calls: int = 10,
    context_tokens: int = 32768,
    output_tokens: int = 1536,
    timeout_s: float = 180,
    seed: int = 42,
    think: bool = False,
) -> dict[str, Any]:
    """Run both arms serially after a same-context model overflow rejection probe."""
    if any(value < 1 for value in (repeats, max_turns, max_mcp_calls, context_tokens, output_tokens)) or timeout_s <= 0:
        raise ValueError("Agent budgets and timeout_s must be positive")
    settings = _Settings(
        endpoint, _local_model_url(model_url), model, repeats, max_turns, max_mcp_calls,
        context_tokens, output_tokens, timeout_s, seed, think,
    )
    dataset = Path(dataset_path)
    tasks = _load_tasks(dataset)
    skill = _load_skill(Path(skill_path))
    output = _output_path(Path(output_dir))
    return anyio.run(_run_comparison, settings, tasks, dataset, skill, output)


async def _api(settings: _Settings, route: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    return await anyio.to_thread.run_sync(
        _api_request, settings.model_url, route, payload,
        settings.timeout_s if timeout is None else timeout,
    )


async def _model_metadata(settings: _Settings, *, full: bool = False) -> dict[str, Any]:
    tags = await _api(settings, "/api/tags")
    name = settings.model if ":" in settings.model else settings.model + ":latest"
    matches = [item for item in tags.get("models", []) if item.get("name") == name or item.get("model") == name]
    if len(matches) != 1 or not isinstance(matches[0].get("digest"), str):
        raise RuntimeError(f"Cannot uniquely resolve local model digest: {name}")
    result = {"model": matches[0], "version": await _api(settings, "/api/version")}
    if full:
        result["show"] = await _api(settings, "/api/show", {"model": settings.model})
        result["running_models"] = await _api(settings, "/api/ps")
    return result


async def _preflight(settings: _Settings, metadata: dict[str, Any]) -> dict[str, Any]:
    if not {"completion", "tools"}.issubset(metadata["show"].get("capabilities", [])):
        raise RuntimeError("Model must advertise completion and native tools capabilities")
    if settings.think and "thinking" not in metadata["show"].get("capabilities", []):
        raise RuntimeError("Model must advertise thinking capability when think=True")
    prompt = "contextcheck " * (settings.context_tokens + 2048)
    request = {
        "model": settings.model, "messages": [{"role": "user", "content": prompt}],
        "tools": [], "think": settings.think, "stream": False, "truncate": False, "shift": False,
        "keep_alive": _KEEP_ALIVE,
        "options": {"temperature": 0, "seed": settings.seed, "num_ctx": settings.context_tokens, "num_predict": 1},
    }
    started = time.perf_counter()
    try:
        response = await _api(settings, "/api/chat", request)
    except _ModelError as exc:
        if "HTTP 400" not in str(exc) or not re.search(r"context|length", _json(exc.response), re.IGNORECASE):
            raise RuntimeError(f"Context overflow preflight did not establish rejection: {exc}") from exc
        return {
            "context_overflow_rejected": True, "http_status": 400,
            "error_response": exc.response, "wall_ms": (time.perf_counter() - started) * 1000,
            "prompt_sha256": _hash(prompt), "prompt_characters": len(prompt),
            "request_controls": {key: value for key, value in request.items() if key != "messages"},
        }
    raise RuntimeError(f"Model silently accepted overflow; truncate/shift controls are not established: {response}")


def _payload(result: Any) -> dict[str, Any]:
    value = result.structuredContent
    if isinstance(value, dict):
        return value
    return {"content": [item.model_dump(mode="json") for item in result.content], "isError": bool(result.isError)}


async def _snapshot(session: ClientSession) -> dict[str, Any]:
    resources = await session.list_resources()
    targets = sorted({str(item.uri).split("/")[2] for item in resources.resources if str(item.uri).endswith("/status")})
    snapshots: dict[str, Any] = {}
    for target in [None, *targets]:
        result = await session.call_tool("codalith_status", {} if target is None else {"target": target})
        value = _payload(result)
        if result.isError or (target is None and not value.get("ready")):
            raise RuntimeError(f"MCP status is unavailable: {value}")
        for corpus in value.get("corpora", []):
            snapshots[corpus["corpus_id"]] = corpus
    return snapshots


def _schedule(tasks: list[dict[str, Any]], repeats: int) -> list[tuple[dict[str, Any], int, str]]:
    return [
        (task, repeat, arm)
        for repeat in range(repeats)
        for index, task in enumerate(tasks)
        for arm in (_ARMS if (index + repeat) % 2 == 0 else _ARMS[::-1])
    ]


async def _run_comparison(settings: _Settings, tasks: list[dict[str, Any]], dataset: Path, skill: _Skill, output: Path) -> dict[str, Any]:
    schedule = _schedule(tasks, settings.repeats)
    rows: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "started_at_utc": _utc(), "settings": vars(settings), "dataset_sha256": _hash(dataset.read_bytes()),
        "skill": {"name": skill.name, "description": skill.description, "sha256": skill.sha256},
        "final_schema": _FINAL_SCHEMA, "final_schema_sha256": _hash(_FINAL_SCHEMA),
        "timeout_semantics": "timeout_s per model HTTP request; case deadline max_turns * timeout_s; citation audit excluded",
        "schedule": [{"id": task["id"], "repeat": repeat, "arm": arm} for task, repeat, arm in schedule],
        "usage_semantics": "Ollama raw prompt_eval_count/eval_count; absent fields remain null; no inferred cache-token accounting",
    }
    infrastructure_errors: list[str] = []
    with (output / "trace.jsonl").open("x", encoding="utf-8") as trace:
        _write(trace, {"event": "run_start", "metadata": metadata})
        try:
            initial_model = await _model_metadata(settings, full=True)
            metadata["model_start"] = initial_model
            metadata["model_preflight"] = await _preflight(settings, initial_model)
            async with streamable_http_client(settings.endpoint) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    native = (await session.list_tools()).tools
                    if {item.name for item in native} != _MCP_TOOLS:
                        raise RuntimeError("Endpoint must expose exactly the seven Codalith MCP tools")
                    tools = [{"type": "function", "function": {"name": item.name, "description": item.description or "", "parameters": item.inputSchema}} for item in native]
                    system = _BASE_SYSTEM + "\n\nMCP server instructions:\n" + (initialized.instructions or "")
                    metadata.update({
                        "mcp_initialize": initialized.model_dump(mode="json"),
                        "native_tools": [item.model_dump(mode="json") for item in native],
                        "native_tools_sha256": _hash([item.model_dump(mode="json") for item in native]),
                        "base_system": system, "base_system_sha256": _hash(system),
                        "arm_system_sha256": {"mcp_only": _hash(system), "skill_mcp": _hash(_skill_system(system, skill))},
                    })
                    baseline = await _snapshot(session)
                    metadata["mcp_start"] = baseline
                    _write(trace, {"event": "initialized", "metadata": metadata})
                    for index, (task, repeat, arm) in enumerate(schedule):
                        if index % 2 == 0:
                            current_model = await _model_metadata(settings)
                            if current_model != {key: initial_model[key] for key in ("model", "version")}:
                                raise RuntimeError("Model digest or model metadata changed during comparison")
                        if await _snapshot(session) != baseline:
                            raise RuntimeError("MCP generation changed before task")
                        skill.read()
                        row = await _run_case(session, settings, task, repeat, arm, system, tools, skill, baseline, trace)
                        rows.append(row)
                        if await _snapshot(session) != baseline:
                            row["error"] = "MCP generation changed after task"
                            _write(trace, {"event": "case_complete", "row": row})
                            raise RuntimeError(row["error"])
                        if index % 2 == 1:
                            current_model = await _model_metadata(settings)
                            if current_model != {key: initial_model[key] for key in ("model", "version")}:
                                row["error"] = "Model digest or model metadata changed after task pair"
                                _write(trace, {"event": "case_complete", "row": row})
                                raise RuntimeError(row["error"])
                        _write(trace, {"event": "case_complete", "row": row})
                    metadata["mcp_end"] = await _snapshot(session)
                    metadata["model_end"] = await _model_metadata(settings, full=True)
                    skill.read()
        except Exception as exc:
            infrastructure_errors.append(f"{type(exc).__name__}: {exc}")
            _write(trace, {"event": "run_error", "error": infrastructure_errors[-1]})
        attempted = len(rows)
        for task, repeat, arm in schedule[attempted:]:
            row = _empty_row(task, repeat, arm)
            row["error"] = "Not run after infrastructure failure"
            row["not_run"] = True
            rows.append(row)
            _write(trace, {"event": "case_not_run", "row": row})
        metadata["finished_at_utc"] = _utc()
        report = {
            "completed": attempted == len(schedule) and not infrastructure_errors,
            "errors": sum(row["error"] is not None for row in rows) + len(infrastructure_errors),
            "infrastructure_errors": infrastructure_errors, "metadata": metadata,
            "summaries": _summaries(rows), "rows": rows,
            "interpretation": "Citation validity and evidence coverage are mechanical checks, not semantic answer correctness. Factual correctness requires blinded review.",
        }
        _write(trace, {"event": "run_complete", "completed": report["completed"], "errors": report["errors"], "summaries": report["summaries"]})
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _skill_system(system: str, skill: _Skill) -> str:
    return system + f"\n\nAvailable skill:\nName: {skill.name}\nDescription: {skill.description}\nUse read_skill to read its instructions when applicable."


def _empty_row(task: dict[str, Any], repeat: int, arm: str) -> dict[str, Any]:
    return {
        "id": task["id"], "category": task.get("category"), "repeat": repeat, "arm": arm,
        "requires_source": task.get("requires_source", False),
        "expected_target": task.get("expected_target"), "expected_facts": task.get("expected_facts", []),
        "expected_abstention": task.get("expected_abstention", {}),
        "expected_tool_behavior": task.get("expected_tool_behavior", {}),
        "started_at_utc": None, "finished_at_utc": None, "wall_ms": None,
        "model_wall_ms": 0.0, "mcp_wall_ms": 0.0, "skill_wall_ms": 0.0, "audit_wall_ms": 0.0,
        "model_turns": 0, "mcp_calls": 0, "status_calls": 0, "source_calls": 0,
        "unknown_calls": 0, "tool_errors": 0, "skill_reads": 0, "skill_read_errors": 0, "skill_read_successes": 0,
        "usage": [], "final": None, "citation_audit": [], "evidence_coverage": [],
        "error": None, "not_run": False,
    }


def _parse_final(content: str) -> dict[str, Any]:
    value = json.loads(content)
    if (
        not isinstance(value, dict)
        or set(value) != {"answer", "citations", "abstained"}
        or not isinstance(value["answer"], str)
        or not isinstance(value["abstained"], bool)
        or not isinstance(value["citations"], list)
    ):
        raise ValueError("Final answer does not match the required JSON schema")
    for citation in value["citations"]:
        if not isinstance(citation, dict) or set(citation) != {"uri", "claim"} or not all(isinstance(item, str) for item in citation.values()):
            raise ValueError("Final citation does not match the required JSON schema")
    return value


def _issued_sources(value: object, found: dict[str, dict[str, Any]], parent: dict[str, Any] | None = None) -> None:
    if isinstance(value, list):
        for item in value:
            _issued_sources(item, found, parent)
    elif isinstance(value, dict):
        context = {**(parent or {}), **{key: value[key] for key in ("corpus_id", "revision", "generation_id", "path", "start_line", "end_line", "sha256", "indexed_sha256", "stale") if key in value}}
        for key in ("uri", "source_uri", "evidence_uri", "target_uri", "root_uri"):
            uri = value.get(key)
            if isinstance(uri, str):
                try:
                    parsed = parse_uri(uri)
                    if parsed.kind == "source" and parsed.start_line is not None:
                        found[uri] = context
                except (ValueError, URIResolutionError):
                    pass
        for item in value.values():
            if isinstance(item, (dict, list)):
                _issued_sources(item, found, context)


async def _run_case(
    session: ClientSession, settings: _Settings, task: dict[str, Any], repeat: int, arm: str,
    system: str, tools: list[dict[str, Any]], skill: _Skill, baseline: dict[str, Any], trace: TextIO,
) -> dict[str, Any]:
    row = _empty_row(task, repeat, arm)
    row["started_at_utc"] = _utc()
    started = time.perf_counter()
    deadline = started + settings.max_turns * settings.timeout_s
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _skill_system(system, skill) if arm == "skill_mcp" else system},
        {"role": "user", "content": task["prompt"]},
    ]
    native_tools = tools + ([skill.tool] if arm == "skill_mcp" else [])
    issued: dict[str, dict[str, Any]] = {}
    identity = {"id": task["id"], "repeat": repeat, "arm": arm}
    _write(trace, {"event": "case_start", **identity, "messages": messages})
    try:
        for turn in range(1, settings.max_turns + 1):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("Agent case wall deadline exceeded")
            request = {
                "model": settings.model, "messages": messages, "tools": native_tools,
                "think": settings.think, "stream": False, "truncate": False, "shift": False,
                "keep_alive": _KEEP_ALIVE,
                "options": {"temperature": 0, "seed": settings.seed, "num_ctx": settings.context_tokens, "num_predict": settings.output_tokens},
            }
            row["model_turns"] = turn
            _write(trace, {"event": "model_request", **identity, "turn": turn, "request": request})
            response: object = None
            api_started = time.perf_counter()
            try:
                response = await _api(settings, "/api/chat", request, min(settings.timeout_s, remaining))
            except Exception as exc:
                response = exc.response if isinstance(exc, _ModelError) else None
                elapsed = (time.perf_counter() - api_started) * 1000
                row["model_wall_ms"] += elapsed
                _write(trace, {"event": "model_response", **identity, "turn": turn, "response": response, "wall_ms": elapsed, "error": f"{type(exc).__name__}: {exc}"})
                row["usage"].append(_usage(response))
                raise
            row["usage"].append(_usage(response))
            elapsed = (time.perf_counter() - api_started) * 1000
            row["model_wall_ms"] += elapsed
            _write(trace, {"event": "model_response", **identity, "turn": turn, "response": response, "wall_ms": elapsed})
            if time.perf_counter() > deadline:
                raise TimeoutError("Agent case wall deadline exceeded")
            if response.get("done_reason") == "length":
                raise RuntimeError("Model output token budget exhausted")
            message = response.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Model response has no assistant message")
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ValueError("Model tool_calls must be an array")
            if not calls:
                row["final"] = _parse_final(message.get("content", ""))
                break
            for call in calls:
                if time.perf_counter() > deadline:
                    raise TimeoutError("Agent case wall deadline exceeded")
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = function.get("name", "") if isinstance(function, dict) else ""
                arguments = function.get("arguments", {}) if isinstance(function, dict) else {}
                if not isinstance(name, str):
                    name = "<invalid-tool-name>"
                tool_started = time.perf_counter()
                if arm == "skill_mcp" and name == "read_skill":
                    row["skill_reads"] += 1
                    if arguments != {"name": skill.name}:
                        result_payload = {"error": "read_skill requires the available skill name"}
                        row["skill_read_errors"] += 1
                    else:
                        result_payload = {"name": skill.name, "content": skill.read(), "sha256": skill.sha256}
                        row["skill_read_successes"] += 1
                    raw_result: object = result_payload
                else:
                    if row["mcp_calls"] >= settings.max_mcp_calls:
                        _write(trace, {"event": "tool_rejected", **identity, "turn": turn, "name": name, "arguments": arguments, "reason": "MCP call budget exhausted"})
                        raise RuntimeError("MCP call budget exhausted")
                    row["mcp_calls"] += 1
                    row["status_calls" if name == "codalith_status" else "source_calls" if name in _MCP_TOOLS else "unknown_calls"] += 1
                    if not isinstance(arguments, dict) or not isinstance(name, str):
                        row["tool_errors"] += 1
                        result_payload = {"error": "Invalid tool name or arguments; arguments must be an object"}
                        raw_result = result_payload
                    else:
                        remaining = deadline - time.perf_counter()
                        try:
                            with anyio.fail_after(max(0, remaining)):
                                result = await session.call_tool(name, arguments)
                            raw_result = result.model_dump(mode="json")
                            result_payload = _payload(result)
                        except McpError as exc:
                            raw_result = result_payload = {"error": f"MCP protocol error: {exc}"}
                            row["tool_errors"] += 1
                        except Exception as exc:
                            elapsed = (time.perf_counter() - tool_started) * 1000
                            row["mcp_wall_ms"] += elapsed
                            row["tool_errors"] += 1
                            _write(trace, {"event": "tool_result", **identity, "turn": turn, "name": name, "arguments": arguments, "wall_ms": elapsed, "error": f"{type(exc).__name__}: {exc}"})
                            raise
                        else:
                            if result.isError:
                                row["tool_errors"] += 1
                            else:
                                _issued_sources(result_payload, issued)
                elapsed = (time.perf_counter() - tool_started) * 1000
                row["skill_wall_ms" if arm == "skill_mcp" and name == "read_skill" else "mcp_wall_ms"] += elapsed
                _write(trace, {"event": "tool_result", **identity, "turn": turn, "name": name, "arguments": arguments, "result": raw_result, "wall_ms": elapsed, "mcp_calls": row["mcp_calls"], "skill_reads": row["skill_reads"]})
                reply = {"role": "tool", "tool_name": name, "content": _json(result_payload)}
                if isinstance(call, dict) and isinstance(call.get("id"), str):
                    reply["tool_call_id"] = call["id"]
                messages.append(reply)
        if row["final"] is None:
            raise RuntimeError("Model turn budget exhausted before a final answer")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["wall_ms"] = (time.perf_counter() - started) * 1000
    row["finished_at_utc"] = _utc()
    row["issued_sources"] = issued
    _write(trace, {"event": "agent_stopped", **identity, "wall_ms": row["wall_ms"], "error": row["error"], "final": row["final"]})
    if row["final"] is not None:
        audit_started = time.perf_counter()
        row["citation_audit"] = await _audit_citations(session, row["final"]["citations"], issued, baseline, trace, identity)
        row["audit_wall_ms"] = (time.perf_counter() - audit_started) * 1000
    row["evidence_coverage"] = _coverage(task, row["citation_audit"])
    return row


def _usage(response: object) -> dict[str, Any]:
    value = response if isinstance(response, dict) else {}
    return {key: value.get(key) for key in ("prompt_eval_count", "eval_count")}


async def _audit_citations(
    session: ClientSession, citations: list[dict[str, str]], issued: dict[str, dict[str, Any]],
    baseline: dict[str, Any], trace: TextIO, identity: dict[str, Any],
) -> list[dict[str, Any]]:
    audits: list[dict[str, Any]] = []
    for citation in citations:
        audit: dict[str, Any] = {**citation, "valid": False, "error": None}
        started = time.perf_counter()
        try:
            uri = citation["uri"]
            if uri not in issued:
                raise ValueError("Citation URI was not issued by a tool during this case")
            parsed = parse_uri(uri)
            result = await session.call_tool("codalith_read", {"uri": uri})
            audit["result"] = result.model_dump(mode="json")
            if result.isError:
                raise ValueError("Citation verification read returned an error")
            source = ReadResponse.model_validate(_payload(result))
            prior = issued[uri]
            corpus = baseline.get(parsed.corpus_id, {})
            if (
                source.uri != uri or source.corpus_id != parsed.corpus_id
                or source.path != parsed.value or source.start_line != parsed.start_line or source.end_line != parsed.end_line
                or source.revision != corpus.get("revision") or source.generation_id != corpus.get("generation_id")
                or source.stale or source.sha256 != source.indexed_sha256
                or re.fullmatch(r"[0-9a-f]{64}", source.sha256) is None
                or any(prior.get(key) is not None and prior[key] != getattr(source, key) for key in ("corpus_id", "revision", "generation_id", "sha256", "indexed_sha256"))
                or prior.get("stale") is True
            ):
                raise ValueError("Citation revision, generation, range, or hash failed verification")
            audit.update({"valid": True, "corpus_id": source.corpus_id, "path": source.path, "start_line": source.start_line, "end_line": source.end_line})
        except Exception as exc:
            audit["error"] = f"{type(exc).__name__}: {exc}"
        audit["wall_ms"] = (time.perf_counter() - started) * 1000
        audits.append(audit)
        _write(trace, {"event": "citation_audit", **identity, "audit": audit})
    return audits


def _coverage(task: dict[str, Any], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": fact["id"],
            "applicable": bool(fact.get("evidence")),
            "covered": any(
                citation["valid"]
                and (task.get("expected_target") is None or citation.get("corpus_id") == task["expected_target"])
                and citation.get("path") == evidence["path"].replace("\\", "/")
                and citation["start_line"] <= evidence["end_line"]
                and citation["end_line"] >= evidence["start_line"]
                for citation in citations for evidence in fact.get("evidence", [])
            ) if fact.get("evidence") else None,
        }
        for fact in task.get("expected_facts", [])
    ]


def _summaries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for arm in _ARMS:
        group = [row for row in rows if row["arm"] == arm]
        facts = [fact for row in group for fact in row["evidence_coverage"]]
        expected_facts = sum(len(row["expected_facts"]) for row in group)
        evidence_facts = sum(bool(fact.get("evidence")) for row in group for fact in row["expected_facts"])
        covered_facts = sum(item["covered"] is True for item in facts)
        citations = [citation for row in group for citation in row["citation_audit"]]
        source_required = [row for row in group if row["expected_tool_behavior"].get("source_query") == "required"]
        source_forbidden = [row for row in group if row["expected_tool_behavior"].get("source_query") == "forbidden"]
        abstentions = [row for row in group if row["expected_abstention"]]
        wall = [row["wall_ms"] for row in group if row["wall_ms"] is not None]
        summary = {
            "planned_cases": len(group), "attempted_cases": sum(not row["not_run"] for row in group),
            "failed_cases": sum(row["error"] is not None for row in group),
            "not_run_cases": sum(row["not_run"] for row in group),
            "final_json_cases": sum(row["final"] is not None for row in group),
            "mcp_calls": sum(row["mcp_calls"] for row in group),
            "source_calls": sum(row["source_calls"] for row in group), "status_calls": sum(row["status_calls"] for row in group),
            "unknown_calls": sum(row["unknown_calls"] for row in group),
            "tool_errors": sum(row["tool_errors"] for row in group),
            "skill_reads": sum(row["skill_reads"] for row in group),
            "skill_activated_cases": sum(row["skill_read_successes"] > 0 for row in group),
            "source_forbidden_skill_activations": sum(row["skill_read_successes"] > 0 for row in source_forbidden),
            "source_forbidden_cases": len(source_forbidden),
            "source_forbidden_violations": sum(row["mcp_calls"] > 0 for row in source_forbidden),
            "source_required_cases": len(source_required),
            "source_required_queried": sum(row["source_calls"] > 0 for row in source_required),
            "citation_count": len(citations), "valid_citations": sum(item["valid"] for item in citations),
            "citation_validity": sum(item["valid"] for item in citations) / len(citations) if citations else None,
            "source_cases_with_valid_citation_rate": sum(any(item["valid"] for item in row["citation_audit"]) for row in source_required) / len(source_required) if source_required else None,
            "expected_facts": expected_facts, "expected_evidence_facts": evidence_facts, "covered_facts": covered_facts,
            "evidence_coverage": covered_facts / evidence_facts if evidence_facts else None,
            "abstention_flag_match_rate": sum(row["final"] is not None and row["final"]["abstained"] == row["expected_abstention"].get("required", False) for row in abstentions) / len(abstentions) if abstentions else None,
            "mean_wall_ms_attempted": sum(wall) / len(wall) if wall else None,
            "model_wall_ms": sum(row["model_wall_ms"] for row in group),
            "mcp_wall_ms": sum(row["mcp_wall_ms"] for row in group),
            "skill_wall_ms": sum(row["skill_wall_ms"] for row in group),
            "audit_wall_ms": sum(row["audit_wall_ms"] for row in group),
        }
        for key in ("prompt_eval_count", "eval_count"):
            usage = [item[key] for row in group for item in row["usage"]]
            known = [value for value in usage if isinstance(value, int) and not isinstance(value, bool)]
            summary[key + "_known_sum"] = sum(known) if known else None
            summary[key + "_missing_requests"] = sum(value is None for value in usage)
        summaries[arm] = summary
    return summaries
