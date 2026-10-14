"""Lossless, narrowly scoped MCP server edits for agent configuration files."""

from __future__ import annotations

import copy
import json
import math
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Node:
    start: int
    end: int
    value: Any
    members: dict[str, _Node] = field(default_factory=dict)
    key_starts: list[int] = field(default_factory=list)
    trailing_comma: bool = False


class _JSONDocument:
    def __init__(self, text: str, *, comments: bool) -> None:
        self.text = text
        self.comments = comments
        self.position = 0
        self.ignored: list[tuple[int, int]] = []
        self.decoder = json.JSONDecoder(parse_constant=self._invalid_constant)
        self.root = self._value()
        self._trivia()
        if self.position != len(text) or not isinstance(self.root.value, dict):
            raise ValueError("Agent configuration must be a valid JSON object")
        sanitized = list(text)
        for start, end in self.ignored:
            for index in range(start, end):
                if sanitized[index] not in "\r\n":
                    sanitized[index] = " "
        decoded = json.loads("".join(sanitized), object_pairs_hook=_unique_object)
        if not _same(decoded, self.root.value):
            raise ValueError("Agent configuration could not be parsed consistently")

    @staticmethod
    def _invalid_constant(value: str) -> None:
        raise ValueError("Non-finite JSON values are not supported")

    def _trivia(self) -> None:
        text = self.text
        while self.position < len(text):
            if text[self.position] in " \t\r\n":
                self.position += 1
            elif self.comments and text.startswith("//", self.position):
                start = self.position
                end = text.find("\n", start + 2)
                self.position = len(text) if end < 0 else end
                self.ignored.append((start, self.position))
            elif self.comments and text.startswith("/*", self.position):
                start = self.position
                end = text.find("*/", start + 2)
                if end < 0:
                    raise ValueError("Agent configuration contains an unfinished comment")
                self.position = end + 2
                self.ignored.append((start, self.position))
            else:
                break

    def _value(self) -> _Node:
        self._trivia()
        start = self.position
        if start >= len(self.text):
            raise ValueError("Agent configuration contains an incomplete value")
        marker = self.text[start]
        if marker in "{[":
            return self._collection(marker)
        value, end = self.decoder.raw_decode(self.text, start)
        self.position = end
        return _Node(start, end, value)

    def _collection(self, marker: str) -> _Node:
        start = self.position
        self.position += 1
        close = "}" if marker == "{" else "]"
        result = _Node(start, start, {} if marker == "{" else [])
        self._trivia()
        if self.position < len(self.text) and self.text[self.position] == close:
            self.position += 1
            result.end = self.position
            return result
        while True:
            self._trivia()
            if marker == "{":
                key_start = self.position
                if self.position >= len(self.text) or self.text[self.position] != '"':
                    raise ValueError("Agent configuration contains an invalid object key")
                key, end = self.decoder.raw_decode(self.text, self.position)
                self.position = end
                self._trivia()
                if self.position >= len(self.text) or self.text[self.position] != ":":
                    raise ValueError("Agent configuration contains an invalid object member")
                self.position += 1
                if key in result.members:
                    raise ValueError("Agent configuration contains duplicate keys")
                child = self._value()
                result.members[key] = child
                result.key_starts.append(key_start)
                result.value[key] = child.value
            else:
                result.value.append(self._value().value)
            self._trivia()
            if self.position >= len(self.text):
                raise ValueError("Agent configuration contains an incomplete collection")
            if self.text[self.position] == close:
                self.position += 1
                result.end = self.position
                return result
            if self.text[self.position] != ",":
                raise ValueError("Agent configuration contains an invalid separator")
            comma = self.position
            self.position += 1
            self._trivia()
            if self.position < len(self.text) and self.text[self.position] == close:
                if not self.comments:
                    raise ValueError("JSON configuration cannot contain trailing commas")
                result.trailing_comma = True
                self.ignored.append((comma, comma + 1))
                self.position += 1
                result.end = self.position
                return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Agent configuration contains duplicate keys")
        result[key] = value
    return result


def _same(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, float) and math.isnan(left) and math.isnan(right):
        return True
    return bool(left == right)


def _matches(current: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(
            key in current and _matches(current[key], value) for key, value in desired.items()
        )
    return _same(current, desired)


def _incompatible_entry(current: Any, desired: dict[str, object]) -> bool:
    if not isinstance(current, dict):
        return False
    if "enabled" not in desired and current.get("enabled") is False:
        return True
    if "disabled" not in desired and current.get("disabled") is True:
        return True
    desired_url = desired.get("url")
    desired_transport = desired.get("type", desired.get("transport", "http"))
    if (
        isinstance(desired_url, str)
        and desired_url.startswith(("http://", "https://"))
        and desired_transport in ("http", "streamable-http", "streamable_http")
    ):
        if "command" in current or "args" in current:
            return True
        return any(current.get(key) in ("stdio", "sse") for key in ("type", "transport"))
    return False


def _expected(
    original: dict[str, Any], path: tuple[str, ...], entry: dict[str, object]
) -> dict[str, Any]:
    result = copy.deepcopy(original)
    parent = result
    for part in path[:-1]:
        parent = parent.setdefault(part, {})
        if not isinstance(parent, dict):
            raise ValueError("MCP server configuration parent must be an object/table")
    parent[path[-1]] = copy.deepcopy(entry)
    return result


def _lookup(original: dict[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    current: Any = original
    for part in path:
        if not isinstance(current, dict):
            raise ValueError("MCP server configuration parent must be an object/table")
        if part not in current:
            return False, None
        current = current[part]
    return True, current


def _line_indent(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    prefix = text[start:position]
    return prefix if not prefix.strip() else ""


def _render_json(value: Any, *, indent: str | None, newline: str) -> str:
    rendered = json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2 if indent is not None else None
    )
    if indent is not None:
        rendered = rendered.replace("\n", newline + indent)
    return rendered


def _insert_json(text: str, node: _Node, key: str, value: Any) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    close = node.end - 1
    multiline = "\n" in text[node.start : node.end]
    if multiline:
        close_indent = _line_indent(text, close)
        member_indent = (
            _line_indent(text, node.key_starts[0]) if node.key_starts else close_indent + "  "
        )
        if not member_indent:
            member_indent = close_indent + "  "
        insertion_at = close - len(close_indent)
        rendered = _render_json(value, indent=member_indent, newline=newline)
        addition = member_indent + json.dumps(key, ensure_ascii=False) + ": " + rendered
        addition += ("," if node.trailing_comma else "") + newline
    else:
        insertion_at = close
        rendered = _render_json(value, indent=None, newline=newline)
        addition = " " + json.dumps(key, ensure_ascii=False) + ": " + rendered
        addition += "," if node.trailing_comma else ""
    edits = [(insertion_at, addition)]
    if node.members and not node.trailing_comma:
        edits.append((next(reversed(node.members.values())).end, ","))
    for position, addition in sorted(edits, key=lambda item: item[0], reverse=True):
        text = text[:position] + addition + text[position:]
    return text


def _merge_json(
    text: str, path: tuple[str, ...], entry: dict[str, object], *, comments: bool
) -> str:
    document = _JSONDocument(text, comments=comments)
    node = document.root
    for index, part in enumerate(path):
        if not isinstance(node.value, dict):
            raise ValueError("MCP server configuration parent must be an object")
        child = node.members.get(part)
        if child is None:
            value: Any = entry
            for remaining in reversed(path[index + 1 :]):
                value = {remaining: value}
            return _insert_json(text, node, part, value)
        if index == len(path) - 1:
            newline = "\r\n" if "\r\n" in text else "\n"
            indent = _line_indent(text, child.start)
            rendered = _render_json(entry, indent=indent if indent else None, newline=newline)
            return text[: child.start] + rendered + text[child.end :]
        node = child
    raise ValueError("MCP server configuration path is empty")


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return (
            "{ "
            + ", ".join(f"{_toml_key(key)} = {_toml_value(item)}" for key, item in value.items())
            + " }"
        )
    raise ValueError("MCP server entry contains an unsupported TOML value")


def _header_path(header: str) -> tuple[str, ...]:
    payload: Any = tomllib.loads(header + "\n__codalith_probe__ = 0\n")
    parts: list[str] = []
    while isinstance(payload, dict) and len(payload) == 1:
        key, payload = next(iter(payload.items()))
        if key == "__codalith_probe__" and payload == 0:
            return tuple(parts)
        parts.append(key)
        if isinstance(payload, list):
            payload = payload[0]
    raise ValueError("TOML table header could not be located safely")


def _toml_headers(text: str) -> list[tuple[int, tuple[str, ...]]]:
    headers: list[tuple[int, tuple[str, ...]]] = []
    position = 0
    state = ""
    while position < len(text):
        if state:
            if text.startswith(state, position):
                position += len(state)
                if len(state) == 3:
                    while position < len(text) and text[position] == state[0]:
                        position += 1
                state = ""
            elif state.startswith('"') and text[position] == "\\":
                position += 2
            else:
                position += 1
            continue
        marker = text[position]
        if marker == "#":
            end = text.find("\n", position)
            position = len(text) if end < 0 else end + 1
        elif marker in "\"'":
            state = marker * 3 if text.startswith(marker * 3, position) else marker
            position += len(state)
        elif marker == "[" and not text[text.rfind("\n", 0, position) + 1 : position].strip():
            start = text.rfind("\n", 0, position) + 1
            end = text.find("\n", position)
            end = len(text) if end < 0 else end
            line = text[position:end]
            try:
                path = _header_path(line)
            except (ValueError, tomllib.TOMLDecodeError):
                position += 1
            else:
                headers.append((start, path))
                position = end
        else:
            position += 1
    return headers


def _merge_toml(text: str, path: tuple[str, ...], entry: dict[str, object], *, exists: bool) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    replacement = "[" + ".".join(_toml_key(part) for part in path) + "]" + newline
    replacement += "".join(
        f"{_toml_key(key)} = {_toml_value(value)}{newline}" for key, value in entry.items()
    )
    if not exists:
        separator = "" if not text else (newline if text.endswith(newline) else newline * 2)
        return text + separator + replacement
    headers = _toml_headers(text)
    if not any(candidate == path for _, candidate in headers):
        raise ValueError(
            "Existing MCP TOML entry uses inline/dotted syntax; safe replacement requires a standard table"
        )
    edits: list[tuple[int, int, str]] = []
    for index, (start, candidate) in enumerate(headers):
        if candidate[: len(path)] != path:
            continue
        end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
        suffix: list[str] = []
        for line in reversed(text[start:end].splitlines(keepends=True)):
            if line.strip() and not line.lstrip().startswith("#"):
                break
            suffix.append(line)
        trivia = "".join(reversed(suffix))
        edits.append((start, end, (replacement if candidate == path else "") + trivia))
    for start, end, addition in reversed(edits):
        text = text[:start] + addition + text[end:]
    return text


def merge_server(
    text: str,
    *,
    format: str,
    server_path: tuple[str, ...],
    name: str,
    entry: dict[str, object],
    replace: bool = False,
) -> str:
    """Merge one server, preserving unrelated bytes and refusing implicit replacement."""
    if format not in {"json", "jsonc", "toml"}:
        raise ValueError("Unsupported agent configuration format")
    if any(not isinstance(part, str) or not part for part in (*server_path, name)):
        raise ValueError("MCP server configuration path contains an invalid name")
    if not isinstance(entry, dict) or any(not isinstance(key, str) for key in entry):
        raise ValueError("MCP server entry must be an object with string keys")
    path = (*server_path, name)
    try:
        json.dumps(entry, allow_nan=False)
        original = (
            tomllib.loads(text)
            if format == "toml"
            else _JSONDocument(text, comments=format == "jsonc").root.value
        )
        exists, current = _lookup(original, path)
        if exists and _matches(current, entry) and not _incompatible_entry(current, entry):
            return text
        if exists and not replace:
            raise ValueError(
                "MCP server entry already exists with different settings; use explicit replacement"
            )
        expected = _expected(original, path, entry)
        merged = (
            _merge_toml(text, path, entry, exists=exists)
            if format == "toml"
            else _merge_json(text, path, entry, comments=format == "jsonc")
        )
        actual = (
            tomllib.loads(merged)
            if format == "toml"
            else _JSONDocument(merged, comments=format == "jsonc").root.value
        )
        if not _same(actual, expected):
            raise ValueError("MCP merge would change unrelated configuration")
        return merged
    except (
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise ValueError("Agent configuration or MCP entry is invalid; no changes made") from exc
