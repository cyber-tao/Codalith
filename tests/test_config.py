from __future__ import annotations

import json

import pytest

from ue_context.config import load_config
from ue_context.errors import ConfigurationError


def test_load_config_expands_environment_placeholders(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        json.dumps(
            {
                "engines": {
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

    assert data["engines"]["ue-5.8.0"]["source_root"] == "/srv/ue/5.8.0"
    assert data["engines"]["ue-5.8.0"]["access_scopes"] == ["ue:default", "source:read"]


def test_load_config_uses_default_for_empty_environment_value(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"source_root": "${UE_TEST_EMPTY_ROOT:-/srv/ue/default}"}), encoding="utf-8")
    monkeypatch.setenv("UE_TEST_EMPTY_ROOT", "")

    assert load_config(path)["source_root"] == "/srv/ue/default"


def test_load_config_rejects_unset_placeholder_without_default(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"source_root": "${UE_TEST_MISSING_ROOT}"}), encoding="utf-8")
    monkeypatch.delenv("UE_TEST_MISSING_ROOT", raising=False)

    with pytest.raises(ConfigurationError, match="UE_TEST_MISSING_ROOT"):
        load_config(path)
