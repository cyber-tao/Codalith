from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.store_manifest import GenerationRepository
from codalith.corpus.uris import source_uri
from codalith.errors import RetrievalError, SourcePolicyError
from codalith.indexing.coderag.backend import store_fingerprint
from codalith.indexing.structure.builder import StructureBuilder
from codalith.languages.catalog import NEUTRAL_PROFILE, UNREAL_PROFILE
from codalith.query.service import (
    QueryService,
    _identifier_candidates,
    _path_search_terms,
    _Request,
    _semantic_pool,
    _type_stem,
)
from conftest import EnvironmentFactory, TestEnvironment, build_environment


def test_search_context_and_canonical_read_are_consistent(
    semantic_environment: TestEnvironment,
) -> None:
    service = semantic_environment.service()
    try:
        search = service.search("Where is CachedValue created?", target="sample")
        assert not search.degraded
        assert search.hits[0].path == "src/core/cache.py"
        assert any(hit.symbol and "CachedValue" in hit.symbol for hit in search.hits)
        context = service.context("Where is CachedValue created?", target="sample")
        assert context.confidence in {"high", "medium"}
        assert not context.degraded
        assert context.sources
        for source in context.sources:
            read = service.read(source.uri)
            assert read.uri == source.uri
            assert read.sha256 == source.sha256
            assert not read.stale
    finally:
        service.close()


def test_exact_text_search_is_filtered_by_structural_membership(
    semantic_environment: TestEnvironment,
) -> None:
    service = semantic_environment.service()
    try:
        assert not service.search(
            "must never enter",
            target="sample",
            strategy="text",
        ).hits
        result = service.search("monotonic", target="sample", strategy="text")
        assert result.hits
        assert {item.path for item in result.hits} == {"src/core/cache.py"}
    finally:
        service.close()


def test_text_search_needs_no_semantic_plane_and_skips_unindexed_matches(
    tmp_path: Path,
) -> None:
    files = {f"aaa/notes-{number:02}.txt": "needle_token here\n" for number in range(60)}
    files["zzz/late.py"] = "VALUE = 'needle_token'\n"
    environment = build_environment(tmp_path, files=files, semantic=False)
    service = environment.service()
    try:
        result = service.search("needle_token", target="sample", strategy="text")
        assert [(hit.path, hit.kind) for hit in result.hits] == [("zzz/late.py", "text")]
    finally:
        service.close()


def test_text_search_failures_degrade_instead_of_failing_the_request(
    semantic_environment: TestEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> list[object]:
        raise RetrievalError("ripgrep text search failed")

    monkeypatch.setattr("codalith.query.service.search_text", fail)
    service = semantic_environment.service()
    try:
        result = service.search("monotonic", target="sample", strategy="text")
        assert result.degraded
        assert "ripgrep text search failed" in result.warnings
    finally:
        service.close()


def test_symbol_graph_has_resolved_source_evidence(
    semantic_environment: TestEnvironment,
) -> None:
    service = semantic_environment.service()
    try:
        symbol = service.symbol("cache_value", target="sample").definitions[0]
        graph = service.graph(symbol.uri, direction="outgoing", depth=1)
        assert any(edge.target_name == "CachedValue" for edge in graph.edges)
        resolved = next(edge for edge in graph.edges if edge.target_name == "CachedValue")
        assert resolved.resolution == "resolved"
        assert resolved.target_uri is not None
        evidence = service.read(resolved.evidence_uri)
        assert "CachedValue" in evidence.text
    finally:
        service.close()


def test_symbol_graph_exposes_module_dependencies(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Source/A/A.Build.cs": (
                "public class A : ModuleRules {\n"
                '    PublicDependencyModuleNames.AddRange(new string[] { "B" });\n'
                "}\n"
            ),
            "Source/B/B.Build.cs": "public class B : ModuleRules {}\n",
        },
        semantic=False,
        corpus_id="modules",
        adapter="cpp-ue",
        include_extensions=(".cs",),
    )
    service = environment.service()
    try:
        module = service.symbol("A", target="modules").definitions[0]
        graph = service.graph(module.uri, direction="outgoing", depth=1)
        edge = next(item for item in graph.edges if item.target_name == "B")
        assert edge.kind == "public"
        assert edge.resolution == "resolved"
        assert edge.target_uri is not None
        assert service.read(edge.evidence_uri).path == "Source/A/A.Build.cs"
    finally:
        service.close()


def test_source_reads_detect_changes_after_indexing(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    service = environment.service()
    try:
        hit = service.search(
            "CachedValue",
            target="sample",
            strategy="symbol",
        ).hits[0]
        assert not service.read(hit.uri).stale
        cache = environment.source_root / "src/core/cache.py"
        cache.write_text(cache.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        assert service.read(hit.uri).stale
        with pytest.raises(SourcePolicyError, match="denies path"):
            service.read(source_uri("sample", ".env"))
    finally:
        service.close()


def test_source_policy_changes_invalidate_the_generation(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    stricter = replace(
        environment.policy,
        deny_globs=(*environment.policy.deny_globs, "**/events.py"),
    )
    service = QueryService(environment.registry, stricter)
    try:
        status = service.status(target="sample")
        assert status.corpora[0].state == "invalid"
        assert "source policy" in (status.corpora[0].message or "")
    finally:
        service.close()


def test_query_outputs_withhold_paths_denied_by_the_source_policy(
    environment_factory: EnvironmentFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = environment_factory(
        semantic=True,
        files={
            "src/core/cache.py": "def cache_value(value):\n    return SecretConfig()\n",
            "src/core/secret_config.py": (
                "class SecretConfig:\n    TOKEN_VALUE = 'top-secret-token-123'\n"
            ),
        },
    )
    original = SourcePolicy.is_denied
    monkeypatch.setattr(
        SourcePolicy,
        "is_denied",
        lambda self, path: path.endswith("secret_config.py") or original(self, path),
    )
    service = environment.service()
    try:
        search = service.search("SecretConfig TOKEN_VALUE", target="sample")
        assert not any(hit.path.endswith("secret_config.py") for hit in search.hits)
        assert any("withheld" in warning for warning in search.warnings)
        assert not service.symbol("SecretConfig", target="sample").definitions
        caller = service.symbol("cache_value", target="sample").definitions[0]
        graph = service.graph(caller.uri, direction="outgoing")
        assert not any(edge.target_name == "SecretConfig" for edge in graph.edges)
        assert graph.warnings
    finally:
        service.close()


def test_symbol_strategy_extracts_identifier_from_a_natural_language_query(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    service = environment.service()
    try:
        result = service.search(
            "Where is CachedValue declared?",
            target="sample",
            strategy="symbol",
        )
        assert result.hits
        assert result.hits[0].symbol == "CachedValue"
    finally:
        service.close()


def test_search_hits_always_fit_the_source_read_policy(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "large.py": "class LargeType:\n" + "    value = 1\n" * 300,
        },
        semantic=False,
    )
    service = environment.service()
    try:
        hit = service.search(
            "LargeType",
            target="sample",
            strategy="symbol",
        ).hits[0]
        assert hit.end_line - hit.start_line + 1 == environment.policy.hard_max_lines
        assert service.read(hit.uri).end_line == hit.end_line
    finally:
        service.close()


def test_context_marks_clamped_and_budget_trimmed_sources_as_truncated(
    tmp_path: Path,
) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "src/big.py": "class BigType:\n" + "    value = 1\n" * 400,
            "src/small.py": "def small_value():\n    return 1\n",
        },
        semantic=False,
    )
    service = environment.service()
    try:
        clamped = service.context("BigType", target="sample", max_chars=100_000)
        big = next(source for source in clamped.sources if source.symbol == "BigType")
        assert big.end_line - big.start_line + 1 == environment.policy.hard_max_lines
        assert big.truncated
        trimmed = service.context("BigType", target="sample", max_chars=1_000)
        assert trimmed.sources[0].truncated
        assert len(trimmed.sources[0].text) <= 1_000
        assert service.read(trimmed.sources[0].uri).text == trimmed.sources[0].text
        complete = service.context("small_value", target="sample")
        small = next(source for source in complete.sources if source.symbol == "small_value")
        assert not small.truncated
    finally:
        service.close()


def test_context_keeps_filling_the_budget_after_a_span_that_cannot_fit(
    tmp_path: Path,
) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "src/a_wide.py": "class WideType:  # " + "x" * 1_500 + "\n    pass\n",
            "src/b_narrow.py": "class NarrowType:\n    pass\n",
        },
        semantic=False,
    )
    service = environment.service()
    try:
        result = service.context("WideType NarrowType", target="sample", max_chars=1_000)
        assert [source.path for source in result.sources] == ["src/b_narrow.py"]
    finally:
        service.close()


def test_symbol_lookup_prefers_type_definitions_over_same_named_fields(
    tmp_path: Path,
) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "fields.py": "class Holder:\n    ValueType = 1\n",
            "types.py": "class ValueType:\n    pass\n",
        },
        semantic=False,
    )
    service = environment.service()
    try:
        hit = service.search(
            "ValueType",
            target="sample",
            strategy="symbol",
        ).hits[0]
        assert hit.kind == "class"
        assert hit.path == "types.py"
    finally:
        service.close()


def test_auto_search_uses_type_name_as_an_exact_file_stem(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Public/Array.h": "template <typename T> class TArray {};\n",
            "Public/Other.h": "template <typename T> class TArray {};\n",
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".h",),
    )
    service = environment.service()
    try:
        hit = service.search("How is TArray declared?", target="sample").hits[0]
        assert hit.path == "Public/Array.h"
        assert service.read(hit.uri).path == "Public/Array.h"
    finally:
        service.close()


def test_auto_search_prefers_a_declaration_header_over_its_source_file(
    tmp_path: Path,
) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Private/Thing.cpp": "FThing::FThing() = default;\n",
            "Public/Thing.h": "class FThing {};\n",
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".cpp", ".h"),
    )
    service = environment.service()
    try:
        hit = service.search("Where is FThing declared?", target="sample").hits[0]
        assert hit.path == "Public/Thing.h"
    finally:
        service.close()


def test_auto_search_uses_multiple_query_terms_to_find_a_file_path(
    tmp_path: Path,
) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Private/reflection.cpp": "void Reflect() {}\n",
            "Public/ObjectMacros.h": "#define GENERATED_BODY()\n",
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".cpp", ".h"),
    )
    service = environment.service()
    try:
        hit = service.search(
            "Which header defines reflection declaration macros?",
            target="sample",
        ).hits[0]
        assert hit.path == "Public/ObjectMacros.h"
    finally:
        service.close()


def test_unreal_profile_resolves_module_rules_files_to_module_symbols(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Plugins/Gizmo/Source/Gizmo/Gizmo.Build.cs": (
                "public class Gizmo : ModuleRules {\n"
                '    PublicDependencyModuleNames.Add("Core");\n'
                "}\n"
            ),
            "Plugins/Gizmo/Source/Gizmo/Private/GizmoModule.cpp": "void StartGizmo() {}\n",
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".cs", ".cpp"),
    )
    service = environment.service()
    try:
        result = service.search("What does Gizmo.Build.cs depend on?", target="sample")
        assert result.hits[0].path == "Plugins/Gizmo/Source/Gizmo/Gizmo.Build.cs"
        assert any(hit.kind == "module" and hit.symbol == "Gizmo" for hit in result.hits)
    finally:
        service.close()


def test_retrieval_conventions_come_only_from_the_corpus_profile() -> None:
    assert _type_stem("FVector", UNREAL_PROFILE) == "Vector"
    assert _type_stem("FVector", NEUTRAL_PROFILE) is None
    assert "engine" not in _path_search_terms("render engine startup", UNREAL_PROFILE)
    assert "engine" in _path_search_terms("render engine startup", NEUTRAL_PROFILE)
    assert _identifier_candidates("Gizmo.Build.cs", UNREAL_PROFILE) == [
        "Gizmo.Build.cs",
        "Gizmo",
    ]
    assert _identifier_candidates("Gizmo.Build.cs", NEUTRAL_PROFILE) == ["Gizmo.Build.cs"]


def test_symbol_search_prefers_exact_case_for_reflection_macros(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Private/Type.cpp": "class UClass {};\n",
            "Public/ObjectMacros.h": "#define UCLASS(...)\n",
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".cpp", ".h"),
    )
    service = environment.service()
    try:
        hit = service.search(
            "UCLASS",
            target="sample",
            strategy="symbol",
        ).hits[0]
        assert hit.path == "Public/ObjectMacros.h"
        assert hit.symbol == "UCLASS"
    finally:
        service.close()


def test_auto_search_resolves_lowercase_symbols_in_macro_context(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Public/AssertionMacros.h": (
                "#define checkf(expr, format, ...) ((void)0)\n#define ensure(expr) (!!(expr))\n"
            ),
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".h",),
    )
    service = environment.service()
    try:
        result = service.search(
            "checkf and ensure assertion macros source",
            target="sample",
        )
        assert result.hits[0].path == "Public/AssertionMacros.h"
        assert {hit.symbol for hit in result.hits} >= {"checkf", "ensure"}
    finally:
        service.close()


def test_text_search_uses_exact_symbol_evidence_for_natural_language(
    tmp_path: Path,
) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "Private/LocalLog.h": "#define UE_LOG(...)\n",
            "Public/Logging/LogMacros.h": "#define UE_LOG(...)\n",
        },
        semantic=False,
        adapter="cpp-ue",
        include_extensions=(".h",),
    )
    service = environment.service()
    try:
        result = service.search(
            "Where is the UE_LOG logging macro defined?",
            target="sample",
            strategy="text",
        )
        assert result.hits[0].path == "Public/Logging/LogMacros.h"
        assert result.hits[0].symbol == "UE_LOG"
    finally:
        service.close()


def test_search_limits_repeated_hits_from_one_file(tmp_path: Path) -> None:
    environment = build_environment(
        tmp_path,
        files={
            "one.py": "def Target(): pass\n" * 8,
            "two.py": "def Target(): pass\n",
        },
        semantic=False,
    )
    service = environment.service()
    try:
        hits = service.search(
            "Target",
            target="sample",
            strategy="symbol",
            limit=10,
        ).hits
        assert sum(hit.path == "one.py" for hit in hits) <= 3
        assert any(hit.path == "two.py" for hit in hits)
    finally:
        service.close()


def test_source_reads_normalize_crlf_without_leaking_carriage_returns(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    cache = environment.source_root / "src/core/cache.py"
    cache.write_bytes(b"first\r\nsecond\r\n")
    StructureBuilder(environment.policy).build(environment.corpus)
    service = environment.service()
    try:
        result = service.read(source_uri("sample", "src/core/cache.py", start_line=1, end_line=2))
        assert result.text == "first\nsecond"
        assert not result.stale
    finally:
        service.close()


def test_requests_keep_their_generation_while_a_new_one_is_published(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    service = environment.service()
    try:
        with _Request(service) as request:
            snapshot = request.snapshot(environment.corpus)
            StructureBuilder(environment.policy).build(environment.corpus)
            newer = service.search("CachedValue", target="sample", strategy="symbol")
            assert newer.hits[0].generation_id != snapshot.generation_id
            assert snapshot.index.lookup_symbols("CachedValue")
        with pytest.raises(sqlite3.ProgrammingError):
            snapshot.index.lookup_symbols("CachedValue")
        read = service.read(newer.hits[0].uri)
        assert read.generation_id == newer.hits[0].generation_id
    finally:
        service.close()


def test_concurrent_queries_survive_generation_publishes(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    service = environment.service()
    errors: list[BaseException] = []
    stop = threading.Event()

    def query_until_stopped() -> None:
        while not stop.is_set():
            try:
                result = service.context("Where is CachedValue created?", target="sample")
                assert result.sources
                assert len({source.generation_id for source in result.sources}) == 1
            except BaseException as exc:
                errors.append(exc)
                return

    workers = [threading.Thread(target=query_until_stopped) for _ in range(4)]
    for worker in workers:
        worker.start()
    try:
        for _ in range(3):
            StructureBuilder(environment.policy).build(environment.corpus)
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=30)
        service.close()
    assert not errors, errors


def test_status_is_side_effect_free_and_reports_semantic_readiness(
    semantic_environment: TestEnvironment,
) -> None:
    service = semantic_environment.service()
    try:
        status = service.status(target="all")
        assert status.ready
        assert status.corpora[0].state == "ready"
        assert status.corpora[0].semantic_available
    finally:
        service.close()


def test_status_distinguishes_missing_from_invalid_generations(tmp_path: Path) -> None:
    environment = build_environment(tmp_path, semantic=False)
    pointer = environment.index_root / "current.json"
    pointer.unlink()
    service = environment.service()
    try:
        assert service.status(target="sample").corpora[0].state == "missing"
        pointer.write_text("{", encoding="utf-8")
        assert service.status(target="sample").corpora[0].state == "invalid"
    finally:
        service.close()


def test_status_rejects_a_generation_with_a_missing_semantic_store(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=True)
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    shutil.rmtree(generation.coderag_path)
    service = environment.service()
    try:
        status = service.status(target="sample")
        assert not status.ready
        assert status.corpora[0].state == "invalid"
        assert "Missing CodeRAG store" in (status.corpora[0].message or "")
    finally:
        service.close()


def test_semantic_pool_scales_with_adopted_files_outside_the_generation(
    semantic_environment: TestEnvironment,
) -> None:
    manifest = (
        GenerationRepository(semantic_environment.policy)
        .active(semantic_environment.corpus)
        .manifest
    )
    assert _semantic_pool(10, manifest) == 40
    assert (
        _semantic_pool(10, replace(manifest, coderag_covered_files=100, coderag_extra_files=300))
        == 160
    )
    assert (
        _semantic_pool(50, replace(manifest, coderag_covered_files=1, coderag_extra_files=10_000))
        == 1_000
    )


def test_semantic_queries_refuse_a_store_with_drifted_provenance(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=True)
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    meta_path = generation.coderag_path / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["embed_model"] = "drifted-model"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = store_fingerprint(generation.coderag_path)
    service = environment.service()
    try:
        status = service.status(target="sample")
        assert status.corpora[0].state == "invalid"
        assert "drifted-model" in (status.corpora[0].message or "")
        with pytest.raises(RetrievalError, match="drifted-model"):
            service.search("CachedValue", target="sample", strategy="semantic")
        assert service.search("CachedValue", target="sample").degraded
    finally:
        service.close()
    assert store_fingerprint(generation.coderag_path) == before


def test_auto_search_reports_missing_semantic_plane_without_creating_a_store(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=False)
    service = environment.service()
    try:
        result = service.search("CachedValue", target="sample")
        assert result.hits
        assert result.degraded
        assert any("Semantic index is unavailable" in item for item in result.warnings)
        assert not GenerationRepository(environment.policy).active(environment.corpus).coderag_path.exists()
    finally:
        service.close()


def test_compare_reports_signature_changes(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        "default_max_lines = 20\nhard_max_lines = 100\nmax_file_bytes = 100000\n"
        'deny_globs = ["**/.env"]\n',
        encoding="utf-8",
    )
    rows: list[str] = ["schema_version = 2", 'default_target = "old"', ""]
    for corpus_id, signature in (("old", "int"), ("new", "str")):
        source = tmp_path / corpus_id / "source"
        source.mkdir(parents=True)
        (source / "api.py").write_text(
            f"def run(value: {signature}) -> {signature}:\n    return value\n",
            encoding="utf-8",
        )
        rows.extend(
            (
                "[[corpora]]",
                f'id = "{corpus_id}"',
                f'revision = "{corpus_id}-v1"',
                f'source_root = "{source.as_posix()}"',
                f'index_root = "{(tmp_path / corpus_id / "index").as_posix()}"',
                'adapter = "python"',
                'embedding_provider = "fake"',
                'include_extensions = [".py"]',
                "exclude_globs = []",
                "",
            )
        )
    registry_path = tmp_path / "registry.toml"
    registry_path.write_text("\n".join(rows), encoding="utf-8")
    registry = CorpusRegistry.from_file(registry_path)
    policy = SourcePolicy.from_file(policy_path)
    builder = StructureBuilder(policy)
    builder.build(registry.get_corpus("old"))
    builder.build(registry.get_corpus("new"))
    service = QueryService(registry, policy)
    try:
        result = service.compare("old", "new")
        change = next(item for item in result.changes if item.comparison_key == "run (function)")
        assert change.status == "changed"
        assert change.changed_fields == ["signature"]
    finally:
        service.close()
