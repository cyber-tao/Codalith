"""Corpus registry for versioned source corpora."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from codalith.config import load_config
from codalith.errors import (
    ConfigurationError,
    CorpusNotFoundError,
    CorpusResolutionError,
)

_SECTION_KINDS = {
    "corpora": "source",
    "projects": "project",
    "generated": "generated",
}


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
    source_revision: str | None = None
    default: bool = False
    access_scopes: frozenset[str] = field(default_factory=frozenset)
    base_corpus: str | None = None
    display_name: str | None = None
    description: str | None = None
    keywords: tuple[str, ...] = ()
    # Maps a search scope name (e.g. "source", "docs") to the path prefixes
    # that belong to it under this corpus.
    scope_prefixes: dict[str, tuple[str, ...]] = field(default_factory=dict)
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
    store_manifest_path: Path | None = None

    @classmethod
    def from_config(
        cls,
        corpus_id: str,
        raw: dict[str, Any],
        *,
        expected_kind: str,
        config_path: Path,
    ) -> Corpus:
        location = f"{config_path}:{corpus_id}"
        kind = _required_string(raw, "kind", location)
        if kind != expected_kind:
            raise ConfigurationError(
                f"{location} kind must be {expected_kind!r}, got {kind!r}"
            )
        version = _optional_string(raw, "version", location)
        source_revision = _optional_string(raw, "source_revision", location)
        if expected_kind == "source":
            if version is None:
                raise ConfigurationError(f"{location} must define a non-empty 'version'")
            if source_revision is None:
                raise ConfigurationError(
                    f"{location} must define a non-empty 'source_revision'"
                )
        default = raw.get("default", False)
        if not isinstance(default, bool):
            raise ConfigurationError(f"{location}.default must be a boolean")
        scope_prefixes_raw = _mapping(raw.get("scope_prefixes", {}), f"{location}.scope_prefixes")
        return cls(
            corpus_id=corpus_id,
            kind=kind,
            version=version,
            source_revision=source_revision,
            source_root=Path(_required_string(raw, "source_root", location)),
            indexed_root=Path(_required_string(raw, "indexed_root", location)),
            coderag_store=Path(_required_string(raw, "coderag_store", location)),
            semantic_schema=_optional_string(raw, "semantic_schema", location)
            or corpus_id.replace("-", "_"),
            card_root=Path(_required_string(raw, "card_root", location)),
            default=default,
            access_scopes=frozenset(
                _string_list(raw.get("access_scopes", []), f"{location}.access_scopes")
            ),
            base_corpus=_optional_string(raw, "base_corpus", location),
            display_name=_optional_string(raw, "display_name", location),
            description=_optional_string(raw, "description", location),
            keywords=tuple(_string_list(raw.get("keywords", []), f"{location}.keywords")),
            scope_prefixes={
                scope: tuple(_string_list(prefixes, f"{location}.scope_prefixes.{scope}"))
                for scope, prefixes in scope_prefixes_raw.items()
            },
            module_roots=tuple(
                _string_list(raw.get("module_roots", []), f"{location}.module_roots")
            ),
            index_ignore_dirs=tuple(
                _string_list(raw.get("index_ignore_dirs", []), f"{location}.index_ignore_dirs")
            ),
            index_suffixes=tuple(
                item.lower()
                for item in _string_list(
                    raw.get("index_suffixes", []), f"{location}.index_suffixes"
                )
            ),
            source_priors_path=Path(value)
            if (value := _optional_string(raw, "source_priors_path", location))
            else None,
            seed_cards_path=Path(value)
            if (value := _optional_string(raw, "seed_cards_path", location))
            else None,
            store_manifest_path=Path(value)
            if (value := _optional_string(raw, "store_manifest_path", location))
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
        config_path = Path(path)
        raw = load_config(config_path)
        loaded: dict[str, dict[str, Corpus]] = {}
        all_ids: set[str] = set()
        for section, expected_kind in _SECTION_KINDS.items():
            section_raw = _mapping(raw.get(section, {}), f"{config_path}:{section}")
            collection: dict[str, Corpus] = {}
            for corpus_id, value in section_raw.items():
                if not corpus_id.strip():
                    raise ConfigurationError(f"{config_path}:{section} contains an empty id")
                if corpus_id in all_ids:
                    raise ConfigurationError(
                        f"{config_path} defines corpus id {corpus_id!r} more than once"
                    )
                corpus_raw = _mapping(value, f"{config_path}:{section}.{corpus_id}")
                collection[corpus_id] = Corpus.from_config(
                    corpus_id,
                    corpus_raw,
                    expected_kind=expected_kind,
                    config_path=config_path,
                )
                all_ids.add(corpus_id)
            loaded[section] = collection
        registry = cls(
            corpora=loaded["corpora"],
            projects=loaded["projects"],
            generated=loaded["generated"],
        )
        registry._validate(config_path)
        return registry

    def _validate(self, config_path: Path) -> None:
        if not self.corpora:
            raise ConfigurationError(f"{config_path} must configure at least one base corpus")
        defaults = [corpus.corpus_id for corpus in self.corpora.values() if corpus.default]
        if len(defaults) != 1:
            raise ConfigurationError(
                f"{config_path} must configure exactly one default base corpus; got {defaults}"
            )
        aliases: dict[str, str] = {}
        for corpus in self.corpora.values():
            assert corpus.version is not None
            existing = aliases.get(corpus.version)
            if existing is not None:
                raise ConfigurationError(
                    f"{config_path} reuses version alias {corpus.version!r} for "
                    f"{existing!r} and {corpus.corpus_id!r}"
                )
            aliases[corpus.version] = corpus.corpus_id
        for collection_name, collection in (
            ("projects", self.projects),
            ("generated", self.generated),
        ):
            for corpus in collection.values():
                if corpus.default:
                    raise ConfigurationError(
                        f"{config_path}:{collection_name}.{corpus.corpus_id} cannot be default"
                    )
                if corpus.base_corpus not in self.corpora:
                    raise ConfigurationError(
                        f"{config_path}:{collection_name}.{corpus.corpus_id} must reference "
                        "an existing base corpus id"
                    )

    def get_corpus(self, corpus_id: str) -> Corpus:
        """Resolve a corpus id (or a corpus version label) to its corpus."""
        for collection in (self.corpora, self.projects, self.generated):
            if corpus_id in collection:
                return collection[corpus_id]
        for corpus in self.corpora.values():
            if corpus.version == corpus_id:
                return corpus
        raise CorpusNotFoundError(f"Unknown corpus: {corpus_id}")

    def get_base(self, corpus: str | None = None) -> Corpus:
        """Resolve a base (non-overlay) corpus by id or version label.

        Without an argument this returns the corpus marked ``default: true``,
        falling back to the first corpus in registry order.
        """
        if corpus:
            if corpus in self.corpora:
                return self.corpora[corpus]
            for configured in self.corpora.values():
                if configured.version == corpus:
                    return configured
            raise CorpusNotFoundError(f"Unknown base corpus: {corpus}")
        for configured in self.corpora.values():
            if configured.default:
                return configured
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
            if corpus.base_corpus == base.corpus_id
        ]

    def resolve(
        self,
        corpus: str | None = None,
        project: str | None = None,
        include_project_overlay: bool = True,
        include_generated_overlay: bool = False,
    ) -> CorpusResolution:
        project_corpus: Corpus | None = None
        if project:
            project_corpus = self.get_project(project)
            assert project_corpus.base_corpus is not None
            base = self.get_base(project_corpus.base_corpus)
            if corpus is not None and self.get_base(corpus).corpus_id != base.corpus_id:
                raise CorpusResolutionError(
                    f"Project {project!r} is bound to {base.corpus_id!r}, "
                    f"not requested corpus {corpus!r}"
                )
        else:
            base = self.get_base(corpus)
        overlays = tuple(self.get_generated_for_base(base)) if include_generated_overlay else ()
        return CorpusResolution(
            base=base,
            project=project_corpus if project and include_project_overlay else None,
            overlays=overlays,
        )


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{location} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ConfigurationError(f"{location} keys must be strings")
    return cast(dict[str, Any], value)


def _required_string(raw: dict[str, Any], key: str, location: str) -> str:
    value = _optional_string(raw, key, location)
    if value is None:
        raise ConfigurationError(f"{location} must define a non-empty {key!r}")
    return value


def _optional_string(raw: dict[str, Any], key: str, location: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{location}.{key} must be a string")
    normalized = value.strip()
    return normalized or None


def _string_list(value: object, location: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigurationError(f"{location} must be an array of strings")
    return [item for item in value if item]
