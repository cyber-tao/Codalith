"""Resolve external codalith:// source URIs against the corpus registry."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.uris import SCHEME, parse_line_fragment
from codalith.errors import CorpusNotFoundError, URIResolutionError


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
        if parsed.scheme != SCHEME:
            raise URIResolutionError(f"Unsupported URI scheme: {parsed.scheme or '<empty>'}")
        try:
            corpus = self.registry.get_corpus(parsed.netloc)
        except CorpusNotFoundError as exc:
            raise URIResolutionError(str(exc)) from exc
        relative_path = self._source_path(parsed.path)
        start_line, end_line = self._line_fragment(parsed.fragment)
        return ResolvedURI(
            uri=uri,
            scheme=SCHEME,
            corpus_id=corpus.corpus_id,
            relative_path=relative_path,
            source_kind=corpus.kind,
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
        try:
            return parse_line_fragment(fragment)
        except ValueError as exc:
            raise URIResolutionError(str(exc)) from exc
