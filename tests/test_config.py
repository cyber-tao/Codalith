from __future__ import annotations

import json

import pytest

from codalith.config import load_config
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.store_manifest import StoreManifest, load_store_manifest
from codalith.errors import ConfigurationError


def test_load_config_expands_environment_placeholders(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "corpora": {
                    "ue-${UE_TEST_VERSION:-5.7.4}": {
                        "source_root": "${UE_TEST_SOURCE_ROOT:-/srv/ue/default}",
                        "access_scopes": ["${UE_TEST_SCOPE:-ue:default}", "source:read"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("UE_TEST_VERSION", "5.8.0")
    monkeypatch.setenv("UE_TEST_SOURCE_ROOT", "/srv/ue/5.8.0")

    data = load_config(path)

    assert data["corpora"]["ue-5.8.0"]["source_root"] == "/srv/ue/5.8.0"
    assert data["corpora"]["ue-5.8.0"]["access_scopes"] == ["ue:default", "source:read"]


def test_load_config_uses_default_for_empty_environment_value(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"source_root": "${UE_TEST_EMPTY_ROOT:-/srv/ue/default}"}), encoding="utf-8")
    monkeypatch.setenv("UE_TEST_EMPTY_ROOT", "")

    assert load_config(path)["source_root"] == "/srv/ue/default"


def test_load_config_rejects_unset_placeholder_without_default(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"source_root": "${UE_TEST_MISSING_ROOT}"}), encoding="utf-8")
    monkeypatch.delenv("UE_TEST_MISSING_ROOT", raising=False)

    with pytest.raises(ConfigurationError, match="UE_TEST_MISSING_ROOT"):
        load_config(path)


def test_load_config_rejects_invalid_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("corpora:\n  ue-5.7.4: {}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="valid JSON"):
        load_config(path)


def test_load_config_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_config(tmp_path / "absent.json")


def test_bundled_configs_are_valid_json():
    assert load_config("configs/corpus_registry.json")["corpora"]
    assert load_config("configs/source_policy.json")["limits"]
    assert load_config("configs/source_priors.json")["priors"]
    ue_registry = CorpusRegistry.from_file("configs/ue_5_7_4_registry.json")
    manifest = load_store_manifest(ue_registry.get_base())
    assert manifest is not None
    assert manifest.embedding_dimension == 4096


def test_registry_requires_source_revision(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "corpora": {
                    "source": {
                        "kind": "source",
                        "version": "v1",
                        "source_root": "source",
                        "indexed_root": "source",
                        "coderag_store": "store",
                        "card_root": "cards",
                        "default": True,
                    }
                },
                "projects": {},
                "generated": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="source_revision"):
        CorpusRegistry.from_file(path)


def test_registry_requires_generated_base_binding(registry_path):
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    raw["generated"]["generated-sample"].pop("base_corpus")
    registry_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="existing base corpus"):
        CorpusRegistry.from_file(registry_path)


def test_registry_rejects_duplicate_version_alias(registry_path):
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    raw["corpora"]["sample-next"]["version"] = "sample"
    registry_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="reuses version alias"):
        CorpusRegistry.from_file(registry_path)


def test_store_manifest_rejects_mismatched_corpus(tmp_path, registry):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "corpus_id": "wrong",
                "source_revision": "TEST",
                "embedding_model": "fake",
                "embedding_dimension": 8,
                "store_schema_version": 1,
                "chunk_policy": {},
            }
        ),
        encoding="utf-8",
    )
    manifest = StoreManifest.from_file(path)

    with pytest.raises(ConfigurationError, match="corpus_id"):
        manifest.validate_corpus(registry.get_base())
