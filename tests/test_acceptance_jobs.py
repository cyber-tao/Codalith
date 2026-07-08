from __future__ import annotations

import json

from jobs.extract_semantic import main as extract_semantic_main
from jobs.index_corpus import main as index_corpus_main


def test_extract_semantic_no_profile_writes_empty_summary(registry_path, tmp_path):
    output = tmp_path / "summary.json"

    assert extract_semantic_main(["--registry", str(registry_path), "--corpus", "sample-codebase", "--output", str(output)]) == 0

    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["corpus_id"] == "sample-codebase"
    assert summary["profile"] is None
    assert summary["modules"] == 0
    assert summary["symbols"] == 0


def test_extract_semantic_with_db_records_corpus(registry_path, tmp_path):
    output = tmp_path / "summary.json"
    db_path = tmp_path / "semantic.sqlite"

    assert extract_semantic_main(
        [
            "--registry",
            str(registry_path),
            "--corpus",
            "sample-codebase",
            "--output",
            str(output),
            "--semantic-db",
            str(db_path),
        ]
    ) == 0

    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["semantic_store"] == str(db_path)
    assert db_path.exists()


def test_index_corpus_smoke_reads_generic_sample(registry_path, capsys):
    assert index_corpus_main(
        [
            "--registry",
            str(registry_path),
            "--corpus",
            "sample-codebase",
            "--smoke",
            "--smoke-file",
            "src/core/cache.py",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["corpus_id"] == "sample-codebase"
    assert payload["smoke_file"] == "src/core/cache.py"
    assert payload["smoke_lines"] > 0
