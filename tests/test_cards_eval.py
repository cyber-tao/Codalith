from __future__ import annotations

import json
from pathlib import Path

from ue_context.cards.generator import built_in_cards, write_cards
from ue_context.cards.schema import CardClaim, KnowledgeCard
from ue_context.cards.verifier import KnowledgeCardVerifier
from ue_context.compiler.context_compiler import ContextCompiler
from ue_context.corpus.uri_resolver import URIResolver
from ue_context.eval.runner import EvalRunner, write_reports


def test_built_in_cards_verify_against_fixture(registry, adapter, tmp_path):
    verifier = KnowledgeCardVerifier(URIResolver(registry), adapter)
    cards = built_in_cards()
    assert len(cards) == 20
    results = [verifier.verify(card) for card in cards]
    assert all(result.ok for result in results), [result.errors for result in results if not result.ok]
    written = write_cards([card.verified() for card in cards], tmp_path)
    assert len(written) == 20
    assert all(path.exists() for path in written)


def test_written_cards_are_searchable_from_indexed_root(registry, adapter, fake_engine_root):
    cards = [card.verified() for card in built_in_cards()]
    write_cards(cards, fake_engine_root)
    adapter.reindex("ue-5.7.4")
    hits = adapter.search_code("ue-5.7.4", "UPROPERTY Replication seed knowledge card", top_k=5)
    assert any("UE_KNOWLEDGE" in hit.path for hit in hits)


def test_card_without_evidence_fails(registry, adapter):
    card = KnowledgeCard(
        corpus_id="ue-5.7.4",
        card_id="bad",
        card_type="mechanism",
        title="Bad",
        version="5.7.4",
        body_markdown="No evidence.",
        claims=[CardClaim(text="Unsupported claim", evidence=[])],
    )
    result = KnowledgeCardVerifier(URIResolver(registry), adapter).verify(card)
    assert not result.ok
    assert result.errors


def test_eval_runner_generates_json_and_markdown(registry, adapter, tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "case-1",
                "query": "UPROPERTY ReplicatedUsing OnRep",
                "expected_files": ["Actor.h"],
                "expected_modules": ["Engine"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(dataset)
    assert report.count == 1
    assert report.file_recall_at_5 == 1.0
    json_path, md_path = write_reports(report, tmp_path / "reports")
    assert json.loads(json_path.read_text(encoding="utf-8"))["count"] == 1
    assert Path(md_path).read_text(encoding="utf-8").startswith("# UE Context Eval Report")
