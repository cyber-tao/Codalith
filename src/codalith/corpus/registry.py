"""Corpus registry for engine and project source roots."""

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
    engine_corpus: str | None = None
    display_name: str | None = None
    description: str | None = None
    keywords: tuple[str, ...] = ()

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
            engine_corpus=raw.get("engine_corpus"),
            display_name=raw.get("display_name"),
            description=raw.get("description"),
            keywords=tuple(str(keyword) for keyword in raw.get("keywords", [])),
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
    engine: Corpus
    project: Corpus | None = None
    overlays: tuple[Corpus, ...] = ()

    @property
    def ordered(self) -> list[Corpus]:
        return [item for item in [self.project, *self.overlays, self.engine] if item is not None]


class CorpusRegistry:
    def __init__(
        self,
        engines: dict[str, Corpus],
        projects: dict[str, Corpus],
        generated: dict[str, Corpus] | None = None,
    ) -> None:
        self.engines = engines
        self.projects = projects
        self.generated = generated or {}

    @classmethod
    def from_file(cls, path: str | Path) -> CorpusRegistry:
        raw = load_config(path)
        engines = {
            corpus_id: Corpus.from_config(corpus_id, value)
            for corpus_id, value in raw.get("engines", {}).items()
        }
        projects = {
            project_id: Corpus.from_config(project_id, value)
            for project_id, value in raw.get("projects", {}).items()
        }
        generated = {
            corpus_id: Corpus.from_config(corpus_id, value)
            for corpus_id, value in raw.get("generated", {}).items()
        }
        return cls(engines=engines, projects=projects, generated=generated)

    def get_engine(self, version: str | None = None) -> Corpus:
        if version:
            if version in self.engines:
                return self.engines[version]
            for corpus in self.engines.values():
                if corpus.version == version:
                    return corpus
            raise CorpusNotFoundError(f"Unknown engine corpus/version: {version}")
        for corpus in self.engines.values():
            if corpus.default:
                return corpus
        if self.engines:
            return next(iter(self.engines.values()))
        raise CorpusNotFoundError("No engine corpus is configured")

    def get_project(self, project: str) -> Corpus:
        if project in self.projects:
            return self.projects[project]
        raise CorpusNotFoundError(f"Unknown project corpus: {project}")

    def get_generated_for_engine(self, engine: Corpus) -> list[Corpus]:
        return [
            corpus
            for corpus in self.generated.values()
            if corpus.engine_corpus in {None, engine.corpus_id}
            or corpus.version == engine.version
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
            engine = self.get_engine(project_corpus.engine_corpus or version)
            overlays = tuple(self.get_generated_for_engine(engine)) if include_generated_overlay else ()
            return CorpusResolution(engine=engine, project=project_corpus, overlays=overlays)
        engine = self.get_engine(version)
        overlays = tuple(self.get_generated_for_engine(engine)) if include_generated_overlay else ()
        return CorpusResolution(engine=engine, overlays=overlays)
