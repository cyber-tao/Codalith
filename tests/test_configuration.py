from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from codalith.config import load_toml
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.store_manifest import Artifact, GenerationRepository
from codalith.corpus.uris import parse_uri, source_uri, symbol_uri
from codalith.errors import (
    ConfigurationError,
    IndexUnavailableError,
    SourcePolicyError,
    URIResolutionError,
)


def test_toml_environment_expansion_and_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_REVISION", "v9")
    path = tmp_path / "nested" / "registry.toml"
    path.parent.mkdir()
    path.write_text(
        'schema_version = 2\ndefault_target = "sample"\n'
        '[[corpora]]\nid = "sample"\nrevision = "${TEST_REVISION}"\n'
        'source_root = "../source"\nindex_root = "../index"\n'
        'adapter = "generic"\ninclude_extensions = [".PY"]\nexclude_globs = []\n',
        encoding="utf-8",
    )
    registry = CorpusRegistry.from_file(path)
    corpus = registry.get_corpus("sample")
    assert corpus.revision == "v9"
    assert corpus.source_root == (tmp_path / "source").resolve()
    assert corpus.include_extensions == (".py",)


def test_unset_environment_variable_is_a_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('value = "${DEFINITELY_UNSET_CODALITH_VAR}"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unset environment variable"):
        load_toml(path)


def test_registry_rejects_unsafe_or_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(
        'schema_version = 2\ndefault_target = "Bad ID"\n'
        '[[corpora]]\nid = "Bad ID"\nrevision = "v1"\n'
        'source_root = "."\nindex_root = "index"\n'
        'adapter = "generic"\ninclude_extensions = []\nexclude_globs = []\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Invalid corpus id"):
        CorpusRegistry.from_file(path)


def test_registry_uses_explicit_path_globs_and_rejects_legacy_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(
        'schema_version = 2\ndefault_target = "sample"\n'
        '[[corpora]]\nid = "sample"\nrevision = "v1"\n'
        'source_root = "."\nindex_root = "index"\n'
        'adapter = "generic"\ninclude_extensions = []\n'
        'exclude_globs = ["Templates/**", "**/ThirdParty/**"]\n',
        encoding="utf-8",
    )
    corpus = CorpusRegistry.from_file(path).get_corpus("sample")
    assert corpus.excludes("Templates/Example.h")
    assert corpus.excludes("Engine/Source/ThirdParty/Library.h")
    assert corpus.excludes("Engine/Source/thirdparty/Library.h")
    assert not corpus.excludes("Engine/Source/Runtime/Core/Public/Templates/Function.h")

    path.write_text(
        path.read_text(encoding="utf-8") + 'ignore_dirs = ["Templates"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="unknown fields: ignore_dirs"):
        CorpusRegistry.from_file(path)


def test_source_policy_blocks_root_nested_and_traversal(
    semantic_environment: object,
) -> None:
    policy = semantic_environment.policy  # type: ignore[attr-defined]
    assert policy.is_denied(".env")
    assert policy.is_denied("nested/.env")
    assert policy.normalize_path("src/core/cache.py") == "src/core/cache.py"
    with pytest.raises(SourcePolicyError, match="Unsafe source path"):
        policy.normalize_path("src/../.env")
    with pytest.raises(SourcePolicyError, match="hard limit"):
        policy.validate_range(1, 201)


def test_canonical_uris_round_trip_and_reject_ambiguous_encodings() -> None:
    uri = source_uri("sample", "路径/a b.py", start_line=2, end_line=4)
    parsed = parse_uri(uri)
    assert parsed.value == "路径/a b.py"
    assert parsed.start_line == 2
    assert parsed.end_line == 4
    assert parsed.canonical == uri
    assert parse_uri(symbol_uri("sample", "abc:1")).value == "abc:1"
    with pytest.raises(URIResolutionError, match="not canonical"):
        parse_uri("codalith://sample/source/src%2Fcore.py#L1-L1")
    with pytest.raises(URIResolutionError, match="cannot precede"):
        source_uri("sample", "x.py", start_line=2, end_line=1)


def test_manifest_validation_rejects_unsafe_artifacts_and_naive_time(
    semantic_environment: object,
) -> None:
    environment = semantic_environment
    generation = GenerationRepository().active(environment.corpus)  # type: ignore[attr-defined]
    manifest = generation.manifest
    with pytest.raises(IndexUnavailableError, match="Unsafe artifact path"):
        replace(
            manifest,
            artifacts=(Artifact("../escape", "0" * 64, 1),),
        ).validate()
    with pytest.raises(IndexUnavailableError, match="timezone"):
        replace(manifest, created_at="2026-07-15T00:00:00").validate()


def test_manifest_loader_rejects_boolean_counts(
    semantic_environment: object,
    tmp_path: Path,
) -> None:
    generation = GenerationRepository().active(semantic_environment.corpus)  # type: ignore[attr-defined]
    payload = generation.manifest.to_dict()
    payload["files"] = True
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IndexUnavailableError, match="counts"):
        type(generation.manifest).load(path)
