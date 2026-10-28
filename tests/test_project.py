from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

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


@pytest.fixture
def engine_repository(tmp_path: Path) -> tuple[Path, str]:
    engine = tmp_path / "engine"
    build = engine / "Engine/Build"
    build.mkdir(parents=True)
    (build / "Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 7, "PatchVersion": 4})
    )

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(engine), *args],
            check=True, capture_output=True,
        ).stdout.decode("ascii").strip()

    git("init")
    git("config", "user.name", "Project test")
    git("config", "user.email", "project-test@example.invalid")
    git("add", "Engine/Build/Build.version")
    git("commit", "-m", "Add engine version fixture")
    return engine, git("rev-parse", "HEAD")


@pytest.mark.parametrize("failure", [
    FileNotFoundError("Git is not installed"),
    subprocess.TimeoutExpired("git", 10),
    subprocess.CompletedProcess(["git"], 129, b"", b"unsupported option"),
    subprocess.CompletedProcess(["git"], 0, b"invalid-revision", b""),
])
def test_engine_git_probe_failure_never_falls_back_to_a_version_match(
    tmp_path: Path,
    engine_repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException | subprocess.CompletedProcess[bytes],
) -> None:
    engine, local_commit = engine_repository
    root = project(tmp_path)
    assert versions()[0]["source_commit"] != local_commit

    def failed_probe(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr("codalith.project.subprocess.run", failed_probe)
    result = detect_project(
        root, engine_root=engine, server="http://localhost/mcp", catalog=versions()
    )
    assert result["status"] == "unavailable"
    assert result["verification"] == "unverified"
    assert "--target" in result["reason"]
    assert "target" not in result
    assert result["source_commit"] is None


def test_git_probe_failure_does_not_treat_an_existing_binding_as_verified(
    tmp_path: Path,
    engine_repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, local_commit = engine_repository
    root = project(tmp_path)
    binding_file = root / BINDING_PATH
    binding_file.parent.mkdir()
    binding_file.write_text(json.dumps({
        "schema_version": 1, "server": "http://localhost/mcp", "target": "ue-574",
        "source_commit": local_commit, "project_file": "Game.uproject",
        "engine_root": str(engine), "engine_version": "5.7.4",
    }))
    rows = versions()
    rows[0]["source_commit"] = local_commit

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Git cannot be executed")

    monkeypatch.setattr("codalith.project.subprocess.run", unavailable)
    result = detect_project(root, catalog=rows)
    assert result["status"] == "unavailable"
    assert result["verification"] == "unverified"
    assert "local source revision is unverified" in result["reason"]


def test_explicit_target_can_bind_after_a_git_probe_failure_but_reports_the_gap(
    tmp_path: Path,
    engine_repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = engine_repository
    root = project(tmp_path)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr("codalith.project.subprocess.run", unavailable)
    detection = detect_project(
        root, engine_root=engine, target="ue-574",
        server="http://localhost/mcp", catalog=versions(),
    )
    assert detection["status"] == "resolved"
    assert detection["verification"] == "unverified"
    assert "remains unverified" in detection["reason"]
    bound = bind_project(root, detection, agents=["codex"], home=tmp_path / "home")
    assert bound["verification"] == "unverified"
    assert bound["reason"] == detection["reason"]


def test_a_verified_engine_revision_filters_same_version_forks(
    tmp_path: Path, engine_repository: tuple[Path, str]
) -> None:
    engine, local_commit = engine_repository
    root = project(tmp_path)
    rows = versions()
    rows.append({"target_id": "custom-574", "engine_version": "5.7.4", "source_commit": local_commit, "ready": True})
    result = detect_project(root, engine_root=engine, server="http://localhost/mcp", catalog=rows)
    assert result["status"] == "resolved"
    assert result["target"] == "custom-574"
    assert result["verification"] == "source_commit"
