from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from codalith.corpus.store_manifest import GenerationRepository
from codalith.errors import IndexBuildError, IndexUnavailableError
from codalith.indexing.coderag.backend import (
    CodeRAGBackend,
    _materialize_source_view,
    preflight_semantic_index,
    prepare_semantic_index,
    store_fingerprint,
)
from codalith.indexing.structure.builder import StructureBuilder
from codalith.indexing.structure.store import StructureIndex
from codalith.indexing.text_search import search_text
from conftest import EnvironmentFactory, TestEnvironment, build_environment


def test_generation_contains_only_policy_selected_files(
    semantic_environment: TestEnvironment,
) -> None:
    generation = GenerationRepository(semantic_environment.policy).active(
        semantic_environment.corpus,
        verify_artifacts=True,
    )
    index = StructureIndex(generation.structure_path)
    try:
        assert [item.path for item in index.list_files()] == [
            "src/core/cache.py",
            "src/core/events.py",
        ]
    finally:
        index.close()
    assert generation.manifest.semantic_available
    assert store_fingerprint(generation.coderag_path) == (
        generation.manifest.coderag_store_fingerprint
    )


def test_semantic_index_cannot_leak_files_outside_the_generation(
    semantic_environment: TestEnvironment,
) -> None:
    generation = GenerationRepository(semantic_environment.policy).active(semantic_environment.corpus)
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


@pytest.mark.parametrize("ripgrep", [True, False])
def test_text_search_matches_the_literal_query_in_indexed_files_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ripgrep: bool,
) -> None:
    if ripgrep and shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed")
    if not ripgrep:
        monkeypatch.setattr("codalith.indexing.text_search.shutil.which", lambda _: None)
    environment = build_environment(
        tmp_path,
        files={
            "nested/source.py": "first line\nWhere is the LITERAL? (exact)\n",
            "notes.md": "Where is the literal? (exact)\n",
        },
        semantic=False,
    )
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    index = StructureIndex(generation.structure_path)
    try:
        hits = search_text(
            environment.corpus,
            index,
            "where is the literal? (exact)",
            limit=10,
            max_file_bytes=environment.policy.max_file_bytes,
        )
    finally:
        index.close()
    assert [(hit.path, hit.line, hit.text) for hit in hits] == [
        ("nested/source.py", 2, "Where is the LITERAL? (exact)")
    ]


def test_resolved_reference_edges_are_stored(
    semantic_environment: TestEnvironment,
) -> None:
    generation = GenerationRepository(semantic_environment.policy).active(semantic_environment.corpus)
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
    first = GenerationRepository(environment.policy).active(environment.corpus)
    (environment.source_root / "src/core/events.py").unlink()
    report = StructureBuilder(environment.policy).build(environment.corpus)
    second = GenerationRepository(environment.policy).active(environment.corpus)
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


def test_adopt_refuses_a_store_built_with_another_embedding_model(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=True)
    store = GenerationRepository(environment.policy).active(environment.corpus).coderag_path
    before = store_fingerprint(store)
    adopted = replace(
        environment.corpus,
        coderag_store=store,
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
    )
    with pytest.raises(IndexBuildError, match="refusing to open"):
        preflight_semantic_index(adopted, environment.policy, mode="adopt")
    with pytest.raises(IndexBuildError, match="refusing to open"):
        prepare_semantic_index(
            adopted,
            store,
            environment.policy,
            mode="adopt",
            indexed_files={"src/core/cache.py": "0" * 64},
        )
    assert store_fingerprint(store) == before


def test_adopt_pins_the_external_store_and_records_coverage(
    environment_factory: EnvironmentFactory,
    tmp_path: Path,
) -> None:
    environment = environment_factory(
        semantic=True,
        files={
            "src/alpha.py": "def alpha():\n    return 1\n",
            "src/beta.py": "def beta():\n    return 2\n",
        },
    )
    repository = GenerationRepository(environment.policy)
    external = tmp_path / "external-store"
    shutil.copytree(repository.active(environment.corpus).coderag_path, external)
    (environment.source_root / "src/beta.py").unlink()
    adopter = replace(environment.corpus, coderag_store=external)
    StructureBuilder(environment.policy).build(adopter, semantic_mode="adopt")
    generation = repository.active(adopter)
    assert generation.coderag_path == external.resolve()
    assert generation.manifest.coderag_covered_files == 1
    assert generation.manifest.coderag_extra_files == 1
    moved = replace(adopter, coderag_store=tmp_path / "another-store")
    assert repository.active(moved).coderag_path == external.resolve()


def test_adopt_rejects_a_store_whose_content_drifted(
    environment_factory: EnvironmentFactory,
    tmp_path: Path,
) -> None:
    environment = environment_factory(semantic=True)
    repository = GenerationRepository(environment.policy)
    original = repository.active(environment.corpus)
    external = tmp_path / "external-store"
    shutil.copytree(original.coderag_path, external)
    cache = environment.source_root / "src/core/cache.py"
    cache.write_text(cache.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    with pytest.raises(IndexBuildError, match="differs from the structural snapshot"):
        StructureBuilder(environment.policy).build(
            replace(environment.corpus, coderag_store=external),
            semantic_mode="adopt",
        )
    assert (
        repository.active(environment.corpus).manifest.generation_id
        == original.manifest.generation_id
    )


def test_build_mode_semantic_plane_covers_every_indexable_file(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Source/Core/Public/Delegate.h": "class FDelegate {};\n",
            "Source/Core/Public/DelegateImpl.inl": "inline void BindDelegate() {}\n",
            "Source/Core/Public/Empty.h": "",
        },
        semantic=True,
        adapter="cpp-ue",
        include_extensions=(".h", ".inl"),
    )
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    assert generation.coderag_path == generation.root / "coderag"
    assert generation.manifest.files == 3
    assert generation.manifest.coderag_covered_files == 2
    assert generation.manifest.coderag_extra_files == 0


def test_build_mode_never_writes_the_configured_external_store(
    environment_factory: EnvironmentFactory,
    tmp_path: Path,
) -> None:
    environment = environment_factory(semantic=False)
    external = tmp_path / "external-store"
    external.mkdir()
    (external / "marker").write_text("keep", encoding="utf-8")
    before = store_fingerprint(external)
    corpus = replace(environment.corpus, coderag_store=external)
    StructureBuilder(environment.policy).build(corpus, semantic_mode="build")
    generation = GenerationRepository(environment.policy).active(corpus)
    assert generation.coderag_path == generation.root / "coderag"
    assert generation.manifest.semantic_available
    assert store_fingerprint(external) == before


def test_semantic_generation_is_invalid_after_a_coderag_upgrade(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=True)
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    payload = json.loads(generation.manifest_path.read_text(encoding="utf-8"))
    payload["coderag_version"] = "0.0.0-previous"
    generation.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IndexUnavailableError, match="built with CodeRAG 0.0.0-previous"):
        GenerationRepository(environment.policy).active(environment.corpus)


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
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    payload = json.loads(generation.manifest_path.read_text(encoding="utf-8"))
    payload["files"] += 1
    generation.manifest_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(IndexUnavailableError, match="does not match"):
        GenerationRepository(environment.policy).active(environment.corpus, verify_artifacts=True)


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
    generation = GenerationRepository(environment.policy).active(environment.corpus)
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
