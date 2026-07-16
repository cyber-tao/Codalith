from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codalith.corpus.store_manifest import GenerationRepository
from codalith.errors import IndexBuildError, IndexUnavailableError
from codalith.indexing.coderag.backend import (
    CodeRAGBackend,
    _materialize_source_view,
    _ripgrep_text_search,
    store_fingerprint,
)
from codalith.indexing.structure.builder import StructureBuilder
from codalith.indexing.structure.store import StructureIndex
from conftest import EnvironmentFactory, TestEnvironment, build_environment


def test_generation_contains_only_policy_selected_files(
    semantic_environment: TestEnvironment,
) -> None:
    generation = GenerationRepository().active(
        semantic_environment.corpus,
        verify_artifacts=True,
    )
    index = StructureIndex(generation.structure_path)
    try:
        assert [item.path for item in index.list_files()] == [
            "src/core/cache.py",
            "src/core/events.py",
        ]
        assert index.integrity_check() == "ok"
        assert index.counts() == generation.manifest.counts
    finally:
        index.close()
    assert generation.manifest.semantic_available
    assert store_fingerprint(generation.coderag_path) == (
        generation.manifest.coderag_store_fingerprint
    )


def test_semantic_index_cannot_leak_files_outside_the_generation(
    semantic_environment: TestEnvironment,
) -> None:
    generation = GenerationRepository().active(semantic_environment.corpus)
    backend = CodeRAGBackend(semantic_environment.policy)
    try:
        hits = backend.search(
            semantic_environment.corpus,
            generation,
            "architecture documentation secret",
            limit=20,
        )
    finally:
        backend.close()
    assert hits
    assert {item.path for item in hits} <= {
        "src/core/cache.py",
        "src/core/events.py",
    }


def test_ripgrep_text_search_treats_natural_language_as_literal(
    tmp_path: Path,
) -> None:
    if shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed")
    environment = build_environment(
        tmp_path,
        files={"source.py": "Where is the literal?\n"},
        semantic=False,
    )
    hits = _ripgrep_text_search(
        environment.corpus,
        "Where is the literal?",
        limit=10,
        max_file_bytes=environment.policy.max_file_bytes,
        deny_globs=environment.policy.deny_globs,
    )
    assert [(hit.path, hit.line) for hit in hits] == [("source.py", 1)]


def test_resolved_reference_edges_are_stored(
    semantic_environment: TestEnvironment,
) -> None:
    generation = GenerationRepository().active(semantic_environment.corpus)
    index = StructureIndex(generation.structure_path)
    try:
        function = index.lookup_symbols("cache_value")[0]
        outgoing = index.references(function.symbol_id, direction="outgoing")
        assert any(
            edge.target_name == "CachedValue"
            and edge.resolution == "resolved"
            and edge.target_symbol_id is not None
            for edge in outgoing
        )
    finally:
        index.close()


def test_new_generation_drops_deleted_source_without_stale_rows(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    first = GenerationRepository().active(environment.corpus)
    (environment.source_root / "src/core/events.py").unlink()
    report = StructureBuilder(environment.policy).build(environment.corpus)
    second = GenerationRepository().active(environment.corpus)
    assert second.manifest.generation_id == report.generation_id
    assert second.manifest.generation_id != first.manifest.generation_id
    index = StructureIndex(second.structure_path)
    try:
        assert index.get_file("src/core/events.py") is None
        assert second.manifest.files == 1
    finally:
        index.close()


def test_failed_build_never_replaces_the_active_pointer(
    environment_factory: EnvironmentFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = environment_factory(semantic=False)
    pointer = environment.index_root / "current.json"
    original = json.loads(pointer.read_text(encoding="utf-8"))["generation_id"]

    def fail(*args: object, **kwargs: object) -> object:
        raise IndexBuildError("forced semantic failure")

    monkeypatch.setattr(
        "codalith.indexing.structure.builder.prepare_semantic_index",
        fail,
    )
    with pytest.raises(IndexBuildError, match="forced semantic failure"):
        StructureBuilder(environment.policy).build(
            environment.corpus,
            semantic_mode="build",
        )
    assert json.loads(pointer.read_text(encoding="utf-8"))["generation_id"] == original
    assert not list((environment.index_root / "generations").glob(".build-*"))


def test_store_fingerprint_detects_same_size_corruption(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    fragment = store / "fragment.lance"
    fragment.write_bytes(b"abcd")
    before = store_fingerprint(store)
    fragment.write_bytes(b"wxyz")
    assert fragment.stat().st_size == 4
    assert store_fingerprint(store) != before


def test_deep_generation_verification_rejects_manifest_count_drift(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    generation = GenerationRepository().active(environment.corpus)
    payload = json.loads(generation.manifest_path.read_text(encoding="utf-8"))
    payload["files"] += 1
    generation.manifest_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(IndexUnavailableError, match="does not match"):
        GenerationRepository().active(environment.corpus, verify_artifacts=True)


def test_index_builder_does_not_follow_source_links(
    environment_factory: EnvironmentFactory,
    tmp_path: Path,
) -> None:
    environment = environment_factory(semantic=False)
    outside = tmp_path / "outside.py"
    outside.write_text("class MustNotLeak:\n    pass\n", encoding="utf-8")
    linked = environment.source_root / "linked.py"
    try:
        linked.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")
    StructureBuilder(environment.policy).build(environment.corpus)
    generation = GenerationRepository().active(environment.corpus)
    index = StructureIndex(generation.structure_path)
    try:
        assert index.get_file("linked.py") is None
        assert not index.lookup_symbols("MustNotLeak")
    finally:
        index.close()


def test_semantic_source_view_copies_when_links_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    view = tmp_path / "view"
    (source / "nested").mkdir(parents=True)
    view.mkdir()
    original = source / "nested/example.py"
    original.write_text("answer = 42\n", encoding="utf-8")

    def reject_link(*args: object, **kwargs: object) -> None:
        raise OSError("links disabled")

    monkeypatch.setattr("codalith.indexing.coderag.backend.os.link", reject_link)
    monkeypatch.setattr(Path, "symlink_to", reject_link)
    _materialize_source_view(source, view, ("nested/example.py",))
    copied = view / "nested/example.py"
    assert copied.read_bytes() == original.read_bytes()
    assert not copied.is_symlink()
