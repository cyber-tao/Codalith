from __future__ import annotations

import json
from pathlib import Path

import pytest

from codalith.project import BINDING_PATH, bind_project, bound_endpoint, detect_project


def project(tmp_path: Path, association: str = "5.7") -> Path:
    root = tmp_path / "game"
    root.mkdir()
    (root / "Game.uproject").write_text(json.dumps({"EngineAssociation": association}))
    return root


def versions() -> list[dict[str, object]]:
    return [
        {"target_id": "ue-574", "engine_version": "5.7.4", "source_commit": "a" * 40, "ready": True},
        {"target_id": "ue-580", "engine_version": "5.8.0", "source_commit": "b" * 40, "ready": True},
    ]


def test_detect_matches_minor_only_when_unambiguous(tmp_path: Path) -> None:
    root = project(tmp_path)
    result = detect_project(root, server="http://localhost:8765/mcp", catalog=versions())
    assert result["status"] == "resolved"
    assert result["target"] == "ue-574"
    result = detect_project(root, server="http://localhost/mcp", catalog=[*versions(), {"target_id": "ue-573", "engine_version": "5.7.3", "source_commit": "c" * 40, "ready": True}])
    assert result["status"] == "ambiguous"


def test_detect_missing_patch_never_substitutes(tmp_path: Path) -> None:
    root = project(tmp_path, "5.7.3")
    result = detect_project(root, server="http://localhost/mcp", catalog=versions())
    assert result["status"] == "unavailable"


def test_multiple_projects_and_path_escape_require_selection(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "Other.uproject").write_text("{}")
    assert detect_project(root)["status"] == "ambiguous"
    outside = root.parent / "Outside.uproject"
    outside.write_text("{}")
    with pytest.raises(ValueError, match="inside"):
        detect_project(root, project_file=outside)


def test_engine_metadata_and_project_association_conflict(tmp_path: Path) -> None:
    root = project(tmp_path)
    engine = tmp_path / "engine"
    build = engine / "Engine/Build"
    build.mkdir(parents=True)
    (build / "Build.version").write_text(json.dumps({"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 0}))
    assert detect_project(root, engine_root=engine, server="http://localhost/mcp", catalog=versions())["status"] == "conflict"


def test_bind_is_atomic_idempotent_and_detects_upgrade(tmp_path: Path) -> None:
    root = project(tmp_path)
    detection = detect_project(root, server="http://localhost:8765/mcp", catalog=versions())
    result = bind_project(root, detection, agents=["codex"], home=tmp_path / "home")
    assert result["endpoint"] == "http://localhost:8765/mcp/targets/ue-574"
    binding = json.loads((root / BINDING_PATH).read_text())
    assert binding["source_commit"] == "a" * 40
    assert bind_project(root, detection, agents=["codex"], home=tmp_path / "home")["changes"] == []
    (root / "Game.uproject").write_text(json.dumps({"EngineAssociation": "5.8"}))
    assert detect_project(root, catalog=versions())["status"] == "conflict"


def test_bind_dry_run_writes_nothing_and_rejects_revision_drift(tmp_path: Path) -> None:
    root = project(tmp_path)
    detection = detect_project(root, server="http://localhost/mcp", catalog=versions())
    bind_project(root, detection, agents=["codex"], home=tmp_path / "home", dry_run=True)
    assert not (root / BINDING_PATH).exists()
    bind_project(root, detection, agents=["codex"], home=tmp_path / "home")
    changed = versions()
    changed[0]["source_commit"] = "d" * 40
    assert detect_project(root, catalog=changed)["status"] == "conflict"


def test_bound_endpoint_preserves_custom_mcp_path_and_rejects_secrets() -> None:
    assert bound_endpoint("http://localhost/custom/mcp/targets/old", "ue-574") == "http://localhost/custom/mcp/targets/ue-574"
    with pytest.raises(ValueError):
        bound_endpoint("http://user:secret@localhost/mcp", "ue-574")
