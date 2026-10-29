from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace

import pytest

from codalith.corpus.generations import GenerationRepository
from codalith.corpus.registry import CorpusRegistry
from codalith.indexing.shared import SharedPool, SharedQueryService
from codalith.semantic.chunking import chunk_file
from codalith.semantic.chunking.languages import detect_language
from codalith.semantic.config import Config
from test_shared_build_efficiency import Source, _environment


def _service_for_backend(environment, backend, files):
    if backend == "directory":
        return environment.service()
    pool = SharedPool(environment.root / "shared-pool", environment.policy)
    pool.build_git_snapshot(
        Source({path: (environment.source_root / path).read_bytes() for path in files}),
        "a" * 40, "version", environment.corpus,
    )
    registry = CorpusRegistry(
        path=environment.registry_path, default_target="version",
        corpora=pool.published_corpora(), workspaces={},
    )
    return SharedQueryService(registry, environment.policy, pool)


@pytest.mark.parametrize(
    ("adapter", "extension", "template", "target"),
    [
        ("python", ".py", "def Foo():\n    return 1\n{extra}def caller():\n    return foo()\n", "foo"),
        ("cpp-ue", ".cpp", "int Foo() {{ return 1; }}\n{extra}int caller() {{ return foo(); }}\n", "foo"),
        ("csharp", ".cs", "partial class C {{ static int Foo() {{ return 1; }}\n{extra}static int caller() {{ return C.foo(); }} }}\n", "C.foo"),
    ],
)
@pytest.mark.parametrize("resolution", ["unresolved", "resolved", "ambiguous"])
@pytest.mark.parametrize("backend", ["directory", "shared"])
def test_reference_resolution_preserves_case(
    environment_factory, adapter, extension, template, target, resolution, backend,
):
    extra = {
        "python": "def foo():\n    return 2\n",
        "cpp-ue": "int foo() { return 2; }\n",
        "csharp": "static int foo() { return 2; }\n",
    }[adapter] if resolution != "unresolved" else ""
    files = {"source" + extension: template.format(extra=extra)}
    if resolution == "ambiguous":
        files["other" + extension] = {
            "python": "def foo(value):\n    return value\n",
            "cpp-ue": "int foo(int value) { return value; }\n",
            "csharp": "partial class C { static int foo(int value) { return value; } }\n",
        }[adapter]
    environment = environment_factory(
        files=files, semantic=False,
        adapter=adapter, include_extensions=(extension,),
    )
    service = _service_for_backend(environment, backend, files)
    try:
        caller = service.symbol("caller").definitions[0]
        edge = next(item for item in service.graph(caller.uri).edges if item.target_name == target)
        assert edge.resolution == resolution
        if resolution == "resolved":
            definition = next(
                item for item in service.symbol("foo").definitions if item.qualified_name == target
            )
            assert edge.target_uri == definition.uri
        else:
            assert edge.target_uri is None
    finally:
        service.close()


@pytest.mark.parametrize("backend", ["directory", "shared"])
def test_parent_resolution_preserves_case(environment_factory, backend):
    files = {"source.py": "class Foo:\n    def first(self): pass\nclass foo:\n    def second(self): pass\n"}
    environment = environment_factory(
        files=files, semantic=False,
    )
    service = _service_for_backend(environment, backend, files)
    try:
        corpus = service.registry.resolve().corpora[0]
        generation = service.generations.active(corpus)
        with closing(service.open_structure(corpus, generation)) as index:
            first = index.lookup_symbols("first")[0]
            second = index.lookup_symbols("second")[0]
            assert index.get_symbol(first.parent_symbol_id).qualified_name == "Foo"
            assert index.get_symbol(second.parent_symbol_id).qualified_name == "foo"
    finally:
        service.close()


@pytest.mark.parametrize(
    ("extension", "text"),
    [
        ("ts", "export function alpha() { return 1; }\nexport function beta() { return 2; }\n"),
        ("js", "function alpha() { return 1; }\nfunction beta() { return 2; }\n"),
        ("tsx", "function Alpha() { return <div />; }\n"),
        ("go", "package sample\nfunc Alpha() int { return 1 }\n"),
        ("rs", "fn alpha() -> i32 { 1 }\n"),
        ("java", "class Example { int alpha() { return 1; } }\n"),
    ],
)
def test_shared_and_directory_chunking_use_the_same_language(tmp_path, extension, text):
    pool, corpus, _ = _environment(tmp_path)
    path = "source." + extension
    profile = replace(corpus, adapter="generic", include_extensions=("." + extension,))
    result = pool.build_git_snapshot(Source({path: text.encode()}), "a" * 40, "version", profile)
    expected = chunk_file(text, detect_language(path), Config(store_dir=tmp_path / "unused"))
    assert any(chunk.symbol is not None for chunk in expected)
    with pool._connect(readonly=True) as connection:
        actual = connection.execute(
            "SELECT language,symbol,kind,start_line,end_line FROM occurrences WHERE snapshot_id=? "
            "ORDER BY start_line,end_line", (result.snapshot_id,),
        ).fetchall()
    assert [tuple(row) for row in actual] == [
        (chunk.language, chunk.symbol, chunk.kind, chunk.start_line, chunk.end_line)
        for chunk in sorted(expected, key=lambda item: (item.start_line, item.end_line))
    ]


@pytest.mark.parametrize("legacy_version", ["1.1.0", "2.0.0"])
def test_explicit_legacy_import_revalidates_older_engine_inputs(
    environment_factory, tmp_path, legacy_version,
):
    text = "def unchanged():\n    return 1\n"
    environment = environment_factory(files={"source.py": text})
    generation = GenerationRepository(environment.policy).active(environment.corpus)
    manifest = json.loads(generation.manifest_path.read_text(encoding="utf-8"))
    manifest["semantic_engine_version"] = legacy_version
    generation.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pool = SharedPool(tmp_path / "pool", environment.policy)
    result = pool.build_git_snapshot(
        Source({"source.py": (environment.source_root / "source.py").read_bytes()}),
        "a" * 40, "version", environment.corpus,
        legacy_generation=generation.root,
    )
    assert result.new_vectors == 0
    assert result.statistics["legacy_imported_vectors"] > 0
