from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codalith.corpus.git_source import GitRepository, GitSourceError


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def make_repository(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init")
    (path / "src").mkdir()
    (path / "src" / "cache.py").write_text("def original():\n    return 57\n", encoding="utf-8")
    (path / "Engine" / "Build").mkdir(parents=True)
    (path / "Engine" / "Build" / "Build.version").write_text(
        '{"MajorVersion":5,"MinorVersion":7,"PatchVersion":4}', encoding="utf-8"
    )
    git(path, "add", "src/cache.py", "Engine/Build/Build.version")
    git(path, "commit", "-m", "Initial source")
    git(path, "tag", "5.7.4")
    (path / "src" / "cache.py").write_text("def updated():\n    return 58\n", encoding="utf-8")
    (path / "Engine" / "Build" / "Build.version").write_text(
        '{"MajorVersion":5,"MinorVersion":8,"PatchVersion":0}', encoding="utf-8"
    )
    git(path, "add", "src/cache.py", "Engine/Build/Build.version")
    git(path, "commit", "-m", "Next source")
    git(path, "tag", "-a", "5.8.0", "-m", "Annotated source release")
    return path


def test_git_source_pins_tags_and_reads_without_changing_checkout(tmp_path: Path) -> None:
    path = make_repository(tmp_path / "repo")
    repository = GitRepository(path)
    first = repository.resolve_tag("5.7.4")
    second = repository.resolve_tag("5.8.0")
    assert first.engine_version == "5.7.4" and not first.annotated
    assert second.engine_version == "5.8.0" and second.annotated
    assert second.tag_oid != second.commit
    before = git(path, "status", "--porcelain")
    files = repository.files(first.commit)
    source = next(item for item in files if item.path == "src/cache.py")
    assert repository.read_blob(source.blob_oid) == b"def original():\n    return 57\n"
    assert list(repository.read_blobs([source.blob_oid])) == [
        (source.blob_oid, repository.read_blob(source.blob_oid))
    ]
    assert git(path, "status", "--porcelain") == before
    assert {tag.tag for tag in repository.tags()} == {"5.7.4", "5.8.0"}


def test_git_source_rejects_object_expressions_and_missing_objects(tmp_path: Path) -> None:
    repository = GitRepository(make_repository(tmp_path / "repo"))
    with pytest.raises(GitSourceError, match="complete Git object ID"):
        repository.read_blob("HEAD:src/cache.py")
    with pytest.raises(GitSourceError, match="missing"):
        repository.read_blob("f" * 40)
    with pytest.raises(GitSourceError):
        repository.resolve_tag("--upload-pack=malicious")


def test_git_tree_missing_blob_has_actionable_error(tmp_path: Path, monkeypatch) -> None:
    repository = GitRepository(make_repository(tmp_path / "repo"))
    monkeypatch.setattr(repository, "_run", lambda *args: b"100644 blob " + b"f" * 40 + b" BAD\tsrc/missing.py\0")
    with pytest.raises(GitSourceError, match="missing or unavailable locally: src/missing.py"):
        repository.files("a" * 40)


def test_git_source_rejects_content_that_does_not_match_object_id(tmp_path: Path, monkeypatch) -> None:
    repository = GitRepository(make_repository(tmp_path / "repo"))
    monkeypatch.setattr(repository, "_run", lambda *args: b"corrupted source bytes")
    with pytest.raises(GitSourceError, match="integrity mismatch"):
        repository.read_blob("a" * 40)


def test_git_source_ignores_replace_refs_and_ambient_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_repository(tmp_path / "repo")
    repository = GitRepository(path)
    first = repository.resolve_tag("5.7.4")
    second = repository.resolve_tag("5.8.0")
    git(path, "replace", first.commit, second.commit)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong"))
    assert repository.resolve_tag("5.7.4").commit == first.commit
    files = repository.files(first.commit)
    source = next(item for item in files if item.path == "src/cache.py")
    assert b"original" in repository.read_blob(source.blob_oid)


def test_git_source_supports_local_bare_repositories(tmp_path: Path) -> None:
    source = make_repository(tmp_path / "repo")
    target = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(target)], check=True, capture_output=True
    )
    assert len(GitRepository(target).tags()) == 2


def test_shallow_repository_and_moved_tag_keep_fixed_commit_reads(tmp_path: Path) -> None:
    source = make_repository(tmp_path / "repo")
    target = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth=1", source.as_uri(), str(target)],
        check=True, capture_output=True,
    )
    assert git(target, "rev-parse", "--is-shallow-repository") == "true"
    repository = GitRepository(target)
    pinned = repository.resolve_tag("5.8.0")
    git(target, "tag", "-f", "5.8.0", pinned.commit)
    files = repository.files(pinned.commit)
    assert files and repository.resolve_tag("5.8.0").tag_oid != pinned.tag_oid
    assert b"updated" in repository.read_blob(next(f.blob_oid for f in files if f.path.endswith("cache.py")))
    with pytest.raises(GitSourceError):
        repository.files(git(source, "rev-parse", "5.7.4"))


def test_git_source_skips_symlink_and_submodule_entries(tmp_path: Path) -> None:
    path = make_repository(tmp_path / "repo")
    commit = git(path, "rev-parse", "HEAD")
    blob = subprocess.run(
        ["git", "-C", str(path), "hash-object", "-w", "--stdin"],
        input=b"../../outside.py", capture_output=True, check=True,
    ).stdout.decode().strip()
    git(path, "update-index", "--add", "--cacheinfo", "120000", blob, "link.py")
    git(path, "update-index", "--add", "--cacheinfo", "160000", commit, "vendor")
    git(path, "commit", "-m", "Non-source entries")
    files = GitRepository(path).files(git(path, "rev-parse", "HEAD"))
    assert {item.path for item in files} == {"src/cache.py", "Engine/Build/Build.version"}
