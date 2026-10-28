from __future__ import annotations

import json
from dataclasses import replace

import pytest

from codalith.corpus.generations import GenerationRepository
from codalith.errors import IndexBuildError
from codalith.indexing.builder import GenerationBuilder
from codalith.indexing.shared import SharedPool
from test_shared_pool import git, repository


def test_shared_selection_supports_empty_and_compound_extensions(environment_factory, tmp_path):
    environment = environment_factory()
    repo, _ = repository(tmp_path)
    (repo.path / "selected.special.py").write_text("def selected():\n    return 3\n", encoding="utf-8")
    git(repo.path, "add", "selected.special.py")
    git(repo.path, "commit", "-qm", "compound suffix")
    commit = git(repo.path, "rev-parse", "HEAD")
    pool = SharedPool(tmp_path / "pool", environment.policy)
    selected = pool.build_git_snapshot(
        repo, commit, "compound", replace(environment.corpus, include_extensions=(".special.py",))
    )
    assert selected.counts["files"] == 1
    all_files = pool.build_git_snapshot(
        repo, commit, "all-python", replace(environment.corpus, include_extensions=())
    )
    assert all_files.counts["files"] == 3


def test_unresolved_lfs_source_fails_without_publishing(environment_factory, tmp_path):
    environment = environment_factory()
    repo, _ = repository(tmp_path)
    (repo.path / "pointer.py").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 100\n",
        encoding="utf-8",
    )
    git(repo.path, "add", "pointer.py")
    git(repo.path, "commit", "-qm", "unresolved source pointer")
    pool = SharedPool(tmp_path / "pool", environment.policy)
    with pytest.raises(IndexBuildError, match="Unresolved Git LFS source"):
        pool.build_git_snapshot(repo, git(repo.path, "rev-parse", "HEAD"), "lfs", environment.corpus)
    assert pool.snapshots() == []


def test_shared_build_reports_actual_source_parse_and_alias_reuse(environment_factory, tmp_path):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    pool = SharedPool(tmp_path / "pool", environment.policy)
    first = pool.build_git_snapshot(repo, revisions[0], "first", environment.corpus)
    second = pool.build_git_snapshot(repo, revisions[1], "second", environment.corpus)
    assert first.statistics["new_source_objects"] == first.statistics["new_parses"] == 2
    assert second.statistics["reused_source_files"] == second.statistics["reused_parses"] == 1
    assert second.statistics["new_source_objects"] == second.statistics["new_parses"] == 1
    alias = pool.build_git_snapshot(repo, revisions[0], "alias", environment.corpus)
    assert alias.snapshot_id == first.snapshot_id
    assert alias.new_vectors == alias.statistics["new_source_objects"] == alias.statistics["new_parses"] == 0
    assert alias.reused_vectors > 0


@pytest.mark.parametrize("incompatible", [False, True])
def test_legacy_missing_or_incompatible_inputs_are_reembedded_and_reported(
    environment_factory, tmp_path, incompatible,
):
    environment = environment_factory()
    repo, revisions = repository(tmp_path)
    for item in repo.files(revisions[0]):
        (environment.source_root / item.path).write_bytes(repo.read_blob(item.blob_oid))
    GenerationBuilder(environment.policy).build(environment.corpus, semantic_mode="build")
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    if incompatible:
        manifest = json.loads(generation.manifest_path.read_text(encoding="utf-8"))
        manifest["embedding_model"] = "different-model"
        generation.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = generation.manifest_path.read_bytes()
    pool = SharedPool(tmp_path / "pool", environment.policy)
    result = pool.build_git_snapshot(
        repo, revisions[1], "imported", environment.corpus, legacy_generation=generation.root,
    )
    statistics = result.statistics
    assert result.new_vectors > 0
    if incompatible:
        assert statistics["legacy_incompatible_inputs"] == result.new_vectors
        assert statistics["legacy_imported_vectors"] == statistics["legacy_missing_inputs"] == 0
    else:
        assert statistics["legacy_imported_vectors"] > 0
        assert statistics["legacy_missing_inputs"] == result.new_vectors
        assert statistics["legacy_incompatible_inputs"] == 0
    assert generation.manifest_path.read_bytes() == before
