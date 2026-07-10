from __future__ import annotations

import json

from codalith.cli.index_corpus import main as index_corpus_main
from codalith.cli.semantic_status import main as semantic_status_main


def test_semantic_status_writes_empty_summary(registry_path, tmp_path):
    output = tmp_path / "summary.json"

    assert (
        semantic_status_main(
            ["--registry", str(registry_path), "--corpus", "sample-codebase", "--output", str(output)]
        )
        == 0
    )

    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["corpus_id"] == "sample-codebase"
    assert summary["modules"] == 0
    assert summary["symbols"] == 0


def test_semantic_status_with_db_records_corpus(registry_path, tmp_path):
    output = tmp_path / "summary.json"
    db_path = tmp_path / "semantic.sqlite"

    assert (
        semantic_status_main(
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
        )
        == 0
    )

    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["semantic_store"] == str(db_path)
    assert db_path.exists()


def test_index_corpus_smoke_reads_generic_sample(registry_path, capsys):
    assert (
        index_corpus_main(
            [
                "--registry",
                str(registry_path),
                "--corpus",
                "sample-codebase",
                "--smoke",
                "--smoke-file",
                "src/core/cache.py",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["corpus_id"] == "sample-codebase"
    assert payload["smoke_file"] == "src/core/cache.py"
    assert payload["smoke_lines"] > 0
