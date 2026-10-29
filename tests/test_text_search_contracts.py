from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tracemalloc
from contextlib import closing

import pytest

from codalith.errors import RetrievalError
from codalith.indexing import text_search
from codalith.indexing.shared import SharedPool
from codalith.indexing.text_search import TextMatchCollector, bounded_snippet
from test_shared_build_efficiency import Source


@pytest.mark.parametrize(
    ("query", "files", "expected"),
    [
        ("Foo", {"a.py": 'value = "foo"\n', "long.py": 'value = "Foo"\n'}, ["long.py"]),
        ("STRASSE", {"a.py": 'value = "Straße"\n', "long.py": 'value = "strasse"\n'}, ["a.py"]),
        ("straße", {"a.py": 'value = "STRASSE"\n', "long.py": 'value = "straße"\n'}, ["long.py"]),
        ("搜索", {"a.py": '# 搜索\n', "long.py": '# 搜索\n'}, ["a.py"]),
        ("Foo", {"a.py": '# foo\n# foo\n# foo\n# Foo\n', "long.py": '# Foo\n'}, ["long.py"]),
        ("foo", {"a.py": '# 中文\n# 中文\n# 中文\n# foo\n'}, ["a.py"]),
        ("[value].*", {"a.py": '# [value].*\n', "b.py": '# value_anything\n'}, ["a.py"]),
    ],
)
@pytest.mark.parametrize("use_rg", [False, True])
def test_text_results_match_across_directory_and_shared(
    environment_factory, tmp_path, monkeypatch, query, files, expected, use_rg,
):
    if use_rg and shutil.which("rg") is None:
        pytest.skip("ripgrep is unavailable")
    environment = environment_factory(files=files, semantic=False)
    if not use_rg:
        monkeypatch.setattr(text_search.shutil, "which", lambda _: None)
    service = environment.service()
    try:
        generation = service.generations.active(environment.corpus)
        with closing(service.open_structure(environment.corpus, generation)) as index:
            directory = text_search.search_text(
                environment.corpus, index, query, limit=1,
                max_file_bytes=environment.policy.max_file_bytes,
            )
        pool = SharedPool(tmp_path / "pool", environment.policy)
        result = pool.build_git_snapshot(
            Source({path: text.encode() for path, text in files.items()}),
            "a" * 40, "version", environment.corpus,
        )
        shared = pool.search_text(
            result.snapshot_id, query, limit=1,
            max_file_bytes=environment.policy.max_file_bytes,
        )
        assert [hit.path for hit in directory] == expected
        assert shared == directory
    finally:
        service.close()


def test_text_collector_retains_bounded_candidate_text():
    tracemalloc.start()
    try:
        collector = TextMatchCollector("needle", 1)
        for index in range(128):
            collector.add(f"{index:04}.py", [(1, "needle" + str(index) + "x" * 65_536)])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert collector.hits()[0].path == "0000.py"
    assert len(collector.hits()[0].text) == 2_000
    assert peak < 1_000_000


def test_text_collector_ranks_the_full_line_before_bounding_the_snippet():
    collector = TextMatchCollector("Foo", 1)
    collector.add("a.py", [(1, "foo")])
    exact_line = "    " + "x" * 4_000 + "Foo"
    collector.add("longname.py", [(1, exact_line)])
    hit = collector.hits()[0]
    assert hit.path == "longname.py"
    assert hit.text == bounded_snippet(exact_line)
    assert len(hit.text) == 2_000
    assert hit.text.endswith("…")


@pytest.mark.parametrize("index_state", ["complete", "incomplete", "missing"])
def test_shared_text_stops_after_a_complete_exact_case_prefix(tmp_path, monkeypatch, index_state):
    from test_shared_build_efficiency import _environment

    pool, corpus, policy = _environment(tmp_path)
    result = pool.build_git_snapshot(
        Source({f"{number:03}.py": b"# needle\n" for number in range(64)}),
        "a" * 40, "version", corpus,
    )
    if index_state != "complete":
        with pool._connect() as connection:
            connection.execute(
                "DELETE FROM source_text_indexed" if index_state == "incomplete"
                else "DROP TABLE source_text_search"
            )
    original = pool._source_inputs
    read_paths = []

    def track(rows, check):
        with closing(original(rows, check)) as inputs:
            for path, data in inputs:
                read_paths.append(path)
                yield path, data

    monkeypatch.setattr(pool, "_source_inputs", track)
    result = pool.search_text(
        result.snapshot_id, "needle", limit=1, max_file_bytes=policy.max_file_bytes,
    )
    assert [hit.path for hit in result] == ["000.py"]
    assert read_paths == ["000.py"]


def test_shared_fts_exact_matches_skip_non_candidates(tmp_path, monkeypatch):
    from test_shared_build_efficiency import _environment

    pool, corpus, policy = _environment(tmp_path)
    result = pool.build_git_snapshot(
        Source({"a.py": b"# unrelated\n", "longname.py": b"# needle\n"}),
        "a" * 40, "version", corpus,
    )
    original = pool._source_inputs
    read_paths = []

    def track(rows, check):
        with closing(original(rows, check)) as inputs:
            for path, data in inputs:
                read_paths.append(path)
                yield path, data

    monkeypatch.setattr(pool, "_source_inputs", track)
    hits = pool.search_text(result.snapshot_id, "needle", limit=1, max_file_bytes=policy.max_file_bytes)
    assert [hit.path for hit in hits] == ["longname.py"]
    assert read_paths == ["longname.py"]


def test_shared_fts_incomplete_exact_results_fall_back_to_unicode(tmp_path):
    from test_shared_build_efficiency import _environment

    pool, corpus, policy = _environment(tmp_path)
    result = pool.build_git_snapshot(
        Source({"a.py": '# Straße\n'.encode(), "long.py": b"# STRASSE\n"}),
        "a" * 40, "version", corpus,
    )
    hits = pool.search_text(result.snapshot_id, "STRASSE", limit=2, max_file_bytes=policy.max_file_bytes)
    assert [(hit.path, hit.text) for hit in hits] == [("long.py", "# STRASSE"), ("a.py", "# Straße")]


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_ripgrep_failure_closes_process(environment_factory, monkeypatch, failure):
    environment = environment_factory(semantic=False)
    original = subprocess.Popen
    processes = []

    def run_child(command, **kwargs):
        source = "import time; time.sleep(10)" if failure == "timeout" else "raise SystemExit(2)"
        process = original([sys.executable, "-c", source], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(text_search.subprocess, "Popen", run_child)
    monkeypatch.setattr(text_search, "_TIMEOUT_SECONDS", 0.05 if failure == "timeout" else 10)
    service = environment.service()
    try:
        generation = service.generations.active(environment.corpus)
        with closing(service.open_structure(environment.corpus, generation)) as index:
            with pytest.raises(RetrievalError, match="timed out" if failure == "timeout" else "failed"):
                list(text_search._ripgrep_matches(
                    environment.corpus, index, "needle", max_file_bytes=1_000_000,
                ))
    finally:
        service.close()
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_ripgrep_stream_does_not_accumulate_output(environment_factory, monkeypatch):
    environment = environment_factory(
        files={f"{index:03}.py": "value = 1\n" for index in range(64)}, semantic=False,
    )
    original = subprocess.Popen
    line = "needle" + "x" * 65_536
    source = (
        "import json\n"
        f"line = {json.dumps(line)}\n"
        "for i in range(64):\n"
        " print(json.dumps({'type':'match','data':{'path':{'text':f'{i:03}.py'},"
        "'lines':{'text':line},'line_number':1}}))\n"
    )

    def run_child(command, **kwargs):
        # Keep the Windows argv below its length limit while generating several MiB of output.
        return original([sys.executable, "-c", source.replace(json.dumps(line), "'needle'+'x'*65536")], **kwargs)

    monkeypatch.setattr(text_search.subprocess, "Popen", run_child)
    service = environment.service()
    tracemalloc.start()
    try:
        generation = service.generations.active(environment.corpus)
        with closing(service.open_structure(environment.corpus, generation)) as index:
            collector = TextMatchCollector("needle", 1)
            for path, lines in text_search._ripgrep_matches(
                environment.corpus, index, "needle", max_file_bytes=1_000_000,
            ):
                collector.add(path, lines)
        _, peak = tracemalloc.get_traced_memory()
        assert collector.hits()[0].path == "000.py"
        assert peak < 2_000_000
    finally:
        tracemalloc.stop()
        service.close()
