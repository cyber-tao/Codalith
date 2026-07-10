"""Codalith-owned provenance contract for a native retrieval store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codalith.config import load_config
from codalith.corpus.registry import Corpus
from codalith.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class StoreManifest:
    corpus_id: str
    source_revision: str
    embedding_model: str
    embedding_dimension: int
    store_schema_version: int
    chunk_policy: dict[str, Any]

    @classmethod
    def from_file(cls, path: str | Path) -> StoreManifest:
        manifest_path = Path(path)
        raw = load_config(manifest_path)
        return cls(
            corpus_id=_required_string(raw, "corpus_id", manifest_path),
            source_revision=_required_string(raw, "source_revision", manifest_path),
            embedding_model=_required_string(raw, "embedding_model", manifest_path),
            embedding_dimension=_required_positive_int(
                raw, "embedding_dimension", manifest_path
            ),
            store_schema_version=_required_positive_int(
                raw, "store_schema_version", manifest_path
            ),
            chunk_policy=_required_mapping(raw, "chunk_policy", manifest_path),
        )

    def validate_corpus(self, corpus: Corpus) -> None:
        if self.corpus_id != corpus.corpus_id:
            raise ConfigurationError(
                f"Store manifest corpus_id {self.corpus_id!r} does not match "
                f"{corpus.corpus_id!r}"
            )
        if self.source_revision != corpus.source_revision:
            raise ConfigurationError(
                f"Store manifest source_revision {self.source_revision!r} does not match "
                f"{corpus.source_revision!r}"
            )


def load_store_manifest(corpus: Corpus) -> StoreManifest | None:
    if corpus.store_manifest_path is None:
        return None
    manifest = StoreManifest.from_file(corpus.store_manifest_path)
    manifest.validate_corpus(corpus)
    return manifest


def _required_string(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{path} must define a non-empty string {key!r}")
    return value.strip()


def _required_positive_int(raw: dict[str, Any], key: str, path: Path) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigurationError(f"{path} must define a positive integer {key!r}")
    return value


def _required_mapping(raw: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must define an object {key!r}")
    return dict(value)
