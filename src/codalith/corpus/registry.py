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
    ue_version: str | None = None
    source_commit: str = "UNKNOWN"
    default: bool = False
    access_scopes: frozenset[str] = field(default_factory=frozenset)
    engine_corpus: str | None = None

    @classmethod
    def from_config(cls, corpus_id: str, raw: dict[str, Any]) -> Corpus:
        return cls(
            corpus_id=corpus_id,
            kind=str(raw["kind"]),
            ue_version=raw.get("ue_version"),
            source_commit=str(raw.get("source_commit", "UNKNOWN")),
            source_root=Path(str(raw["source_root"])),
            indexed_root=Path(str(raw["indexed_root"])),
            coderag_store=Path(str(raw["coderag_store"])),
            semantic_schema=str(raw.get("semantic_schema", corpus_id.replace("-", "_"))),
            card_root=Path(str(raw["card_root"])),
            default=bool(raw.get("default", False)),
            access_scopes=frozenset(str(scope) for scope in raw.get("access_scopes", [])),
            engine_corpus=raw.get("engine_corpus"),
        )


@dataclass(frozen=True, slots=True)
class CorpusResolution:
    engine: Corpus
    project: Corpus | None = None

    @property
    def ordered(self) -> list[Corpus]:
        return [self.project, self.engine] if self.project else [self.engine]


class CorpusRegistry:
    def __init__(self, engines: dict[str, Corpus], projects: dict[str, Corpus]) -> None:
        self.engines = engines
        self.projects = projects

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
        return cls(engines=engines, projects=projects)

    def get_engine(self, version: str | None = None) -> Corpus:
        if version:
            candidates = [version, f"ue-{version}" if not version.startswith("ue-") else version]
            for corpus_id in candidates:
                if corpus_id in self.engines:
                    return self.engines[corpus_id]
            for corpus in self.engines.values():
                if corpus.ue_version == version:
                    return corpus
            raise CorpusNotFoundError(f"Unknown UE engine corpus/version: {version}")
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

    def resolve(
        self,
        version: str | None = None,
        project: str | None = None,
        include_project_overlay: bool = True,
    ) -> CorpusResolution:
        if project and include_project_overlay:
            project_corpus = self.get_project(project)
            engine = self.get_engine(project_corpus.engine_corpus or version)
            return CorpusResolution(engine=engine, project=project_corpus)
        return CorpusResolution(engine=self.get_engine(version))
