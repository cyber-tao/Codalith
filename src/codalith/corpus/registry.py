"""Validated corpus and workspace registry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from codalith.config import load_toml, resolve_config_path
from codalith.corpus.globs import matches_path
from codalith.errors import ConfigurationError, CorpusNotFoundError

REGISTRY_SCHEMA_VERSION = 2
_SAFE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_ADAPTERS = frozenset({"python", "csharp", "cpp-ue", "generic"})
_PROVIDERS = frozenset({"fake", "fastembed", "openai"})
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "default_target", "corpora", "workspaces"})
_CORPUS_FIELDS = frozenset(
    {
        "id",
        "display_name",
        "description",
        "revision",
        "source_root",
        "index_root",
        "coderag_store",
        "adapter",
        "embedding_provider",
        "embedding_model",
        "include_extensions",
        "exclude_globs",
    }
)
_WORKSPACE_FIELDS = frozenset({"id", "corpora"})


@dataclass(frozen=True, slots=True)
class Corpus:
    corpus_id: str
    display_name: str
    description: str
    revision: str
    source_root: Path
    index_root: Path
    coderag_store: Path | None
    adapter: str
    embedding_provider: str
    embedding_model: str
    include_extensions: tuple[str, ...]
    exclude_globs: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.display_name} ({self.revision})"

    def excludes(self, path: str) -> bool:
        canonical = path.replace("\\", "/").lstrip("/")
        return any(matches_path(canonical, pattern) for pattern in self.exclude_globs)


@dataclass(frozen=True, slots=True)
class Workspace:
    workspace_id: str
    corpus_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Target:
    target_id: str
    corpora: tuple[Corpus, ...]


class CorpusRegistry:
    def __init__(
        self,
        *,
        path: Path,
        default_target: str,
        corpora: dict[str, Corpus],
        workspaces: dict[str, Workspace],
    ) -> None:
        self.path = path
        self.default_target = default_target
        self.corpora = corpora
        self.workspaces = workspaces
        self.resolve(default_target)

    @classmethod
    def from_file(cls, path: str | Path) -> CorpusRegistry:
        config_path, payload = load_toml(path)
        schema_version = payload.get("schema_version")
        if schema_version != REGISTRY_SCHEMA_VERSION:
            raise ConfigurationError(
                f"{config_path} schema_version must be {REGISTRY_SCHEMA_VERSION}"
            )
        _reject_unknown(payload, _TOP_LEVEL_FIELDS, "registry", config_path)
        default_target = _required_string(payload, "default_target", config_path)
        raw_corpora = payload.get("corpora")
        if not isinstance(raw_corpora, list) or not raw_corpora:
            raise ConfigurationError(f"{config_path} must define at least one [[corpora]]")
        corpora: dict[str, Corpus] = {}
        for index, raw in enumerate(raw_corpora):
            if not isinstance(raw, dict):
                raise ConfigurationError(f"{config_path} corpora[{index}] must be a table")
            corpus = _parse_corpus(config_path, raw, index)
            if corpus.corpus_id in corpora:
                raise ConfigurationError(f"Duplicate corpus id: {corpus.corpus_id}")
            corpora[corpus.corpus_id] = corpus

        workspaces: dict[str, Workspace] = {}
        raw_workspaces = payload.get("workspaces", [])
        if not isinstance(raw_workspaces, list):
            raise ConfigurationError(f"{config_path} workspaces must be an array of tables")
        for index, raw in enumerate(raw_workspaces):
            if not isinstance(raw, dict):
                raise ConfigurationError(
                    f"{config_path} workspaces[{index}] must be a table"
                )
            _reject_unknown(raw, _WORKSPACE_FIELDS, f"workspaces[{index}]", config_path)
            workspace_id = _safe_id(_required_string(raw, "id", config_path), "workspace id")
            if workspace_id in corpora or workspace_id in workspaces:
                raise ConfigurationError(f"Target id is not unique: {workspace_id}")
            corpus_ids = _string_tuple(raw.get("corpora"), "workspace corpora", config_path)
            if not corpus_ids:
                raise ConfigurationError(f"Workspace {workspace_id} must contain a corpus")
            missing = [item for item in corpus_ids if item not in corpora]
            if missing:
                raise ConfigurationError(
                    f"Workspace {workspace_id} references unknown corpora: {', '.join(missing)}"
                )
            if len(set(corpus_ids)) != len(corpus_ids):
                raise ConfigurationError(f"Workspace {workspace_id} contains duplicate corpora")
            workspaces[workspace_id] = Workspace(workspace_id, corpus_ids)
        return cls(
            path=config_path,
            default_target=default_target,
            corpora=corpora,
            workspaces=workspaces,
        )

    def resolve(self, target: str | None = None) -> Target:
        target_id = (target or self.default_target).strip()
        corpus = self.corpora.get(target_id)
        if corpus is not None:
            return Target(target_id, (corpus,))
        workspace = self.workspaces.get(target_id)
        if workspace is not None:
            return Target(
                target_id,
                tuple(self.corpora[item] for item in workspace.corpus_ids),
            )
        raise CorpusNotFoundError(f"Unknown corpus or workspace: {target_id}")

    def get_corpus(self, corpus_id: str) -> Corpus:
        try:
            return self.corpora[corpus_id]
        except KeyError as exc:
            raise CorpusNotFoundError(f"Unknown corpus: {corpus_id}") from exc


def _parse_corpus(path: Path, raw: dict[str, Any], index: int) -> Corpus:
    _reject_unknown(raw, _CORPUS_FIELDS, f"corpora[{index}]", path)
    corpus_id = _safe_id(_required_string(raw, "id", path), "corpus id")
    adapter = _required_string(raw, "adapter", path)
    if adapter not in _ADAPTERS:
        raise ConfigurationError(
            f"Corpus {corpus_id} adapter must be one of {', '.join(sorted(_ADAPTERS))}"
        )
    provider = str(raw.get("embedding_provider", "fastembed")).strip()
    if provider not in _PROVIDERS:
        raise ConfigurationError(
            f"Corpus {corpus_id} embedding_provider must be one of "
            f"{', '.join(sorted(_PROVIDERS))}"
        )
    revision = _required_string(raw, "revision", path)
    if not revision.strip():
        raise ConfigurationError(f"Corpus {corpus_id} revision cannot be blank")
    source_root = resolve_config_path(path, _required_string(raw, "source_root", path))
    index_root = resolve_config_path(path, _required_string(raw, "index_root", path))
    raw_coderag_store = raw.get("coderag_store")
    if raw_coderag_store is not None and (
        not isinstance(raw_coderag_store, str) or not raw_coderag_store.strip()
    ):
        raise ConfigurationError(f"Corpus {corpus_id} coderag_store must be a path string")
    coderag_store = (
        resolve_config_path(path, raw_coderag_store)
        if isinstance(raw_coderag_store, str)
        else None
    )
    raw_extensions = _string_tuple(
        raw.get("include_extensions", []),
        f"corpus {corpus_id} include_extensions",
        path,
    )
    include_extensions: list[str] = []
    for extension in raw_extensions:
        if not extension.startswith(".") or "/" in extension or "\\" in extension:
            raise ConfigurationError(
                f"Corpus {corpus_id} has invalid extension {extension!r}"
            )
        normalized_extension = extension.casefold()
        if normalized_extension in include_extensions:
            raise ConfigurationError(
                f"Corpus {corpus_id} has duplicate extension {extension!r}"
            )
        include_extensions.append(normalized_extension)
    exclude_globs = _glob_tuple(
        raw.get("exclude_globs", []),
        f"corpus {corpus_id} exclude_globs",
        path,
    )
    return Corpus(
        corpus_id=corpus_id,
        display_name=str(raw.get("display_name", corpus_id)).strip() or corpus_id,
        description=str(raw.get("description", "")).strip(),
        revision=revision,
        source_root=source_root,
        index_root=index_root,
        coderag_store=coderag_store,
        adapter=adapter,
        embedding_provider=provider,
        embedding_model=str(raw.get("embedding_model", "")).strip(),
        include_extensions=tuple(include_extensions),
        exclude_globs=exclude_globs,
    )


def _required_string(mapping: dict[str, Any], key: str, path: Path) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{path} field {key!r} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, label: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{path} {label} must be an array")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigurationError(f"{path} {label} must contain non-empty strings")
        items.append(item.strip())
    return tuple(items)


def _glob_tuple(value: Any, label: str, path: Path) -> tuple[str, ...]:
    items = _string_tuple(value, label, path)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        glob = item.replace("\\", "/").lstrip("/")
        if not glob or any(part == ".." for part in PurePosixPath(glob).parts):
            raise ConfigurationError(f"{path} {label} contains unsafe glob {item!r}")
        folded = glob.casefold()
        if folded in seen:
            raise ConfigurationError(f"{path} {label} contains duplicate glob {item!r}")
        seen.add(folded)
        normalized.append(glob)
    return tuple(normalized)


def _reject_unknown(
    mapping: dict[str, Any],
    allowed: frozenset[str],
    location: str,
    path: Path,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigurationError(
            f"{path} {location} contains unknown fields: {', '.join(unknown)}"
        )


def _safe_id(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ConfigurationError(
            f"Invalid {label} {value!r}; use lowercase URI-safe characters"
        )
    return value
