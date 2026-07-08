"""Corpus registry for versioned source corpora."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codalith.config import load_config
from codalith.errors import CorpusNotFoundError


@dataclass(frozen=True, slots=True)
class Corpus:
    corpus_id: str
    kind: str
    source_root: Path
    indexed_root: Path
    coderag_store: Path
    semantic_schema: str
    card_root: Path
    version: str | None = None
    source_commit: str = "UNKNOWN"
    default: bool = False
    access_scopes: frozenset[str] = field(default_factory=frozenset)
    base_corpus: str | None = None
    display_name: str | None = None
    description: str | None = None
    keywords: tuple[str, ...] = ()
    # Maps a search scope name (e.g. "source", "docs") to the path prefixes
    # that belong to it; scopes without prefixes do not filter by path.
    scope_prefixes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Name of the domain extractor profile used to build the semantic graph.
    semantic_profile: str | None = None
    # Directory names whose next path segment is the module name. Empty means
    # no path-based module hints.
    module_roots: tuple[str, ...] = ()
    # Extra directory names skipped while indexing, on top of the built-in
    # neutral ignores (VCS/store internals).
    index_ignore_dirs: tuple[str, ...] = ()
    # Extra file suffixes indexed by the local fallback, on top of the
    # built-in plain-text set.
    index_suffixes: tuple[str, ...] = ()
    # Optional corpus-local retrieval/card domain data. Defaults should stay
    # empty so the service core never assumes a specific source domain.
    source_priors_path: Path | None = None
    seed_cards_path: Path | None = None

    @classmethod
    def from_config(cls, corpus_id: str, raw: dict[str, Any]) -> Corpus:
        return cls(
            corpus_id=corpus_id,
            kind=str(raw["kind"]),
            version=raw.get("version"),
            source_commit=str(raw.get("source_commit", "UNKNOWN")),
            source_root=Path(str(raw["source_root"])),
            indexed_root=Path(str(raw["indexed_root"])),
            coderag_store=Path(str(raw["coderag_store"])),
            semantic_schema=str(raw.get("semantic_schema", corpus_id.replace("-", "_"))),
            card_root=Path(str(raw["card_root"])),
            default=bool(raw.get("default", False)),
            access_scopes=frozenset(str(scope) for scope in raw.get("access_scopes", [])),
            base_corpus=raw.get("base_corpus"),
            display_name=raw.get("display_name"),
            description=raw.get("description"),
            keywords=tuple(str(keyword) for keyword in raw.get("keywords", [])),
            scope_prefixes={
                str(scope): tuple(str(prefix) for prefix in prefixes)
                for scope, prefixes in raw.get("scope_prefixes", {}).items()
            },
            semantic_profile=raw.get("semantic_profile"),
            module_roots=tuple(str(item) for item in raw.get("module_roots", [])),
            index_ignore_dirs=tuple(str(item) for item in raw.get("index_ignore_dirs", [])),
            index_suffixes=tuple(str(item).lower() for item in raw.get("index_suffixes", [])),
            source_priors_path=Path(str(raw["source_priors_path"]))
            if raw.get("source_priors_path")
            else None,
            seed_cards_path=Path(str(raw["seed_cards_path"]))
            if raw.get("seed_cards_path")
            else None,
        )

    @property
    def version_label(self) -> str:
        """Client-facing version label, falling back to the corpus id."""
        return self.version or self.corpus_id

    @property
    def label(self) -> str:
        """Client-facing display label, falling back to the corpus id."""
        if self.display_name:
            return f"{self.display_name} {self.version}" if self.version else self.display_name
        return self.corpus_id


@dataclass(frozen=True, slots=True)
class CorpusResolution:
    base: Corpus
    project: Corpus | None = None
    overlays: tuple[Corpus, ...] = ()

    @property
    def ordered(self) -> list[Corpus]:
        return [item for item in [self.project, *self.overlays, self.base] if item is not None]


class CorpusRegistry:
    def __init__(
        self,
        corpora: dict[str, Corpus],
        projects: dict[str, Corpus],
        generated: dict[str, Corpus] | None = None,
    ) -> None:
        self.corpora = corpora
        self.projects = projects
        self.generated = generated or {}

    @classmethod
    def from_file(cls, path: str | Path) -> CorpusRegistry:
        raw = load_config(path)
        corpora = {
            corpus_id: Corpus.from_config(corpus_id, value)
            for corpus_id, value in raw.get("corpora", {}).items()
        }
        projects = {
            project_id: Corpus.from_config(project_id, value)
            for project_id, value in raw.get("projects", {}).items()
        }
        generated = {
            corpus_id: Corpus.from_config(corpus_id, value)
            for corpus_id, value in raw.get("generated", {}).items()
        }
        return cls(corpora=corpora, projects=projects, generated=generated)

    def get_corpus(self, corpus_id: str) -> Corpus:
        """Resolve a corpus id (or a corpus version label) to its corpus."""
        for collection in (self.corpora, self.projects, self.generated):
            if corpus_id in collection:
                return collection[corpus_id]
        for corpus in self.corpora.values():
            if corpus.version == corpus_id:
                return corpus
        raise CorpusNotFoundError(f"Unknown corpus: {corpus_id}")

    def get_base(self, version: str | None = None) -> Corpus:
        """Resolve a base (non-overlay) corpus by id or version label.

        Without an argument this returns the corpus marked ``default: true``,
        falling back to the first corpus in registry order.
        """
        if version:
            if version in self.corpora:
                return self.corpora[version]
            for corpus in self.corpora.values():
                if corpus.version == version:
                    return corpus
            raise CorpusNotFoundError(f"Unknown corpus/version: {version}")
        for corpus in self.corpora.values():
            if corpus.default:
                return corpus
        if self.corpora:
            return next(iter(self.corpora.values()))
        raise CorpusNotFoundError("No base corpus is configured")

    def get_project(self, project: str) -> Corpus:
        if project in self.projects:
            return self.projects[project]
        raise CorpusNotFoundError(f"Unknown project corpus: {project}")

    def get_generated_for_base(self, base: Corpus) -> list[Corpus]:
        return [
            corpus
            for corpus in self.generated.values()
            if corpus.base_corpus in {None, base.corpus_id} or corpus.version == base.version
        ]

    def resolve(
        self,
        version: str | None = None,
        project: str | None = None,
        include_project_overlay: bool = True,
        include_generated_overlay: bool = False,
    ) -> CorpusResolution:
        if project and include_project_overlay:
            project_corpus = self.get_project(project)
            base = self.get_base(project_corpus.base_corpus or version)
            overlays = tuple(self.get_generated_for_base(base)) if include_generated_overlay else ()
            return CorpusResolution(base=base, project=project_corpus, overlays=overlays)
        base = self.get_base(version)
        overlays = tuple(self.get_generated_for_base(base)) if include_generated_overlay else ()
        return CorpusResolution(base=base, overlays=overlays)
