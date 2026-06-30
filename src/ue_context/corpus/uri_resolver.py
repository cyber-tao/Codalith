"""External UE URI resolver."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import ParseResult, unquote, urlparse

from ue_context.corpus.registry import CorpusRegistry
from ue_context.errors import URIResolutionError

_LINE_RE = re.compile(r"^L(?P<start>[1-9]\d*)(?:-L?(?P<end>[1-9]\d*))?$")


@dataclass(frozen=True, slots=True)
class ResolvedURI:
    uri: str
    scheme: str
    corpus_id: str
    relative_path: str
    source_kind: str
    start_line: int | None = None
    end_line: int | None = None

    @property
    def line_count(self) -> int | None:
        if self.start_line is None or self.end_line is None:
            return None
        return self.end_line - self.start_line + 1


class URIResolver:
    def __init__(self, registry: CorpusRegistry) -> None:
        self.registry = registry

    def resolve_source(self, uri: str) -> ResolvedURI:
        parsed = urlparse(uri)
        if parsed.scheme == "ue":
            return self._resolve_engine(parsed, uri)
        if parsed.scheme == "ue-project":
            return self._resolve_project(parsed, uri)
        raise URIResolutionError(f"Unsupported URI scheme: {parsed.scheme or '<empty>'}")

    def _resolve_engine(self, parsed: ParseResult, uri: str) -> ResolvedURI:
        netloc = parsed.netloc
        path = parsed.path
        fragment = parsed.fragment
        version = netloc.removeprefix("ue-")
        corpus = self.registry.get_engine(version)
        relative_path = self._source_path(path)
        start_line, end_line = self._line_fragment(fragment)
        return ResolvedURI(
            uri=uri,
            scheme="ue",
            corpus_id=corpus.corpus_id,
            relative_path=relative_path,
            source_kind="engine",
            start_line=start_line,
            end_line=end_line,
        )

    def _resolve_project(self, parsed: ParseResult, uri: str) -> ResolvedURI:
        project = parsed.netloc
        path = parsed.path
        fragment = parsed.fragment
        corpus = self.registry.get_project(project)
        relative_path = self._source_path(path)
        start_line, end_line = self._line_fragment(fragment)
        return ResolvedURI(
            uri=uri,
            scheme="ue-project",
            corpus_id=corpus.corpus_id,
            relative_path=relative_path,
            source_kind="project",
            start_line=start_line,
            end_line=end_line,
        )

    @staticmethod
    def _source_path(path: str) -> str:
        prefix = "/source/"
        if not path.startswith(prefix):
            raise URIResolutionError("Only source URIs can be resolved to files")
        relative = unquote(path[len(prefix) :]).replace("\\", "/").lstrip("/")
        if not relative or ".." in relative.split("/"):
            raise URIResolutionError(f"Invalid source path: {relative!r}")
        return relative

    @staticmethod
    def _line_fragment(fragment: str) -> tuple[int | None, int | None]:
        if not fragment:
            return None, None
        match = _LINE_RE.match(fragment)
        if not match:
            raise URIResolutionError(f"Invalid line fragment: #{fragment}")
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if end < start:
            raise URIResolutionError(f"Invalid descending line range: #{fragment}")
        return start, end


def make_source_uri(version_or_project: str, path: str, start: int, end: int, *, project: bool = False) -> str:
    scheme = "ue-project" if project else "ue"
    return f"{scheme}://{version_or_project}/source/{path}#L{start}-L{end}"
