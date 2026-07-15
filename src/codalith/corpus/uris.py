"""Canonical Codalith URI construction and parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from codalith.errors import URIResolutionError

_CORPUS_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_LINE_FRAGMENT = re.compile(r"^L([1-9][0-9]*)(?:-L([1-9][0-9]*))?$")


@dataclass(frozen=True, slots=True)
class CodalithURI:
    corpus_id: str
    kind: str
    value: str
    start_line: int | None = None
    end_line: int | None = None

    @property
    def canonical(self) -> str:
        if self.kind == "source":
            return source_uri(
                self.corpus_id,
                self.value,
                start_line=self.start_line,
                end_line=self.end_line,
            )
        if self.kind == "symbol":
            return symbol_uri(self.corpus_id, self.value)
        if self.kind == "status":
            return status_uri(self.corpus_id)
        raise URIResolutionError(f"Unsupported Codalith URI kind: {self.kind}")


def source_uri(
    corpus_id: str,
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    _validate_corpus_id(corpus_id)
    encoded_path = _encode_path(path)
    fragment = ""
    if start_line is not None:
        if start_line < 1:
            raise URIResolutionError("Source URI start line must be at least 1")
        effective_end = end_line or start_line
        if effective_end < start_line:
            raise URIResolutionError("Source URI end line cannot precede start line")
        fragment = f"#L{start_line}-L{effective_end}"
    elif end_line is not None:
        raise URIResolutionError("Source URI end line requires a start line")
    return f"codalith://{corpus_id}/source/{encoded_path}{fragment}"


def symbol_uri(corpus_id: str, symbol_id: str) -> str:
    _validate_corpus_id(corpus_id)
    if not symbol_id or "/" in symbol_id or symbol_id in {".", ".."}:
        raise URIResolutionError("Invalid symbol id")
    return f"codalith://{corpus_id}/symbol/{quote(symbol_id, safe='')}"


def status_uri(corpus_id: str) -> str:
    _validate_corpus_id(corpus_id)
    return f"codalith://{corpus_id}/status"


def parse_uri(uri: str) -> CodalithURI:
    parsed = urlsplit(uri)
    if parsed.scheme != "codalith" or not parsed.netloc or parsed.query:
        raise URIResolutionError(f"Invalid Codalith URI: {uri}")
    _validate_corpus_id(parsed.netloc)
    parts = parsed.path.lstrip("/").split("/", 1)
    kind = parts[0]
    start: int | None = None
    end: int | None = None
    if parsed.fragment:
        match = _LINE_FRAGMENT.fullmatch(parsed.fragment)
        if kind != "source" or match is None:
            raise URIResolutionError(f"Invalid source line fragment: {parsed.fragment}")
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        if end < start:
            raise URIResolutionError("Source URI end line cannot precede start line")
    if kind == "status" and len(parts) == 1:
        result = CodalithURI(parsed.netloc, kind, "")
    elif kind in {"source", "symbol"} and len(parts) == 2 and parts[1]:
        value = unquote(parts[1], errors="strict")
        if kind == "source":
            value = _decode_path(value)
        elif "/" in value or value in {".", ".."}:
            raise URIResolutionError("Invalid symbol id")
        result = CodalithURI(parsed.netloc, kind, value, start, end)
    else:
        raise URIResolutionError(f"Unsupported Codalith URI: {uri}")
    if _normalized_percent_encoding(result.canonical) != _normalized_percent_encoding(uri):
        raise URIResolutionError(f"URI is not canonical: {uri}")
    return result


def _encode_path(path: str) -> str:
    canonical = _decode_path(path.replace("\\", "/"))
    return "/".join(quote(segment, safe="-._~") for segment in canonical.split("/"))


def _decode_path(path: str) -> str:
    if not path or path.startswith("/"):
        raise URIResolutionError("Source path must be relative")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise URIResolutionError("Source path contains an unsafe segment")
    return "/".join(parts)


def _validate_corpus_id(corpus_id: str) -> None:
    if not _CORPUS_ID.fullmatch(corpus_id):
        raise URIResolutionError(f"Invalid corpus id: {corpus_id}")


def _normalized_percent_encoding(value: str) -> str:
    return re.sub(r"%[0-9a-fA-F]{2}", lambda item: item.group(0).upper(), value)
