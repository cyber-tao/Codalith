from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.store_manifest import GenerationRepository
from codalith.errors import SourcePolicyError
from codalith.indexing.structure.builder import StructureBuilder
from codalith.query.service import QueryService
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
                "    PublicDependencyModuleNames.AddRange(new string[] { \"B\" });\n"
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
            service.sources.read("sample", ".env")
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
        result = service.sources.read(
            "sample",
            "src/core/cache.py",
            start_line=1,
            end_line=2,
        )
        assert result.text == "first\nsecond"
        assert not result.stale
    finally:
        service.close()


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


def test_status_rejects_a_generation_with_a_missing_semantic_store(
    environment_factory: EnvironmentFactory,
) -> None:
    environment = environment_factory(semantic=True)
    generation = GenerationRepository().active(environment.corpus)
    shutil.rmtree(generation.coderag_path)
    service = environment.service()
    try:
        status = service.status(target="sample")
        assert not status.ready
        assert status.corpora[0].state == "invalid"
        assert "Missing CodeRAG store" in (status.corpora[0].message or "")
    finally:
        service.close()


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
        assert not GenerationRepository().active(environment.corpus).coderag_path.exists()
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
                'exclude_globs = []',
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
