from __future__ import annotations

import json
from pathlib import Path

from jobs.extract_semantic import extract_semantic_summary

from codalith.cards.generator import attach_source_hashes, built_in_cards, write_cards
from codalith.cards.schema import CardClaim, KnowledgeCard
from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.uri_resolver import URIResolver
from codalith.eval.runner import EvalRunner, write_reports
from codalith.semantic.db import SemanticStore


def test_built_in_cards_verify_against_fixture(registry, adapter, tmp_path):
    verifier = KnowledgeCardVerifier(URIResolver(registry), adapter)
    cards = attach_source_hashes(built_in_cards(), URIResolver(registry), adapter)
    assert len(cards) == 20
    assert all(card.source_hashes for card in cards)
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


def test_card_hash_mismatch_fails(registry, adapter):
    resolver = URIResolver(registry)
    card = attach_source_hashes(built_in_cards()[:1], resolver, adapter)[0]
    bad_card = KnowledgeCard.from_dict(
        {
            **card.as_dict(),
            "source_hashes": {uri: "0" * 64 for uri in card.source_hashes},
        }
    )

    result = KnowledgeCardVerifier(resolver, adapter).verify(bad_card)

    assert not result.ok
    assert any("hash mismatch" in error for error in result.errors)


def test_card_verifier_checks_related_semantic_nodes(registry, adapter, fake_engine_root, tmp_path):
    store = SemanticStore(tmp_path / "semantic.sqlite")
    extract_semantic_summary(fake_engine_root, corpus_id="ue-5.7.4", store=store)
    resolver = URIResolver(registry)
    card = attach_source_hashes(built_in_cards()[:1], resolver, adapter)[0]

    result = KnowledgeCardVerifier(resolver, adapter, store).verify(card)

    assert result.ok

    bad_card = KnowledgeCard.from_dict(
        {
            **card.as_dict(),
            "related_nodes": ["module:MissingModule"],
        }
    )
    bad_result = KnowledgeCardVerifier(resolver, adapter, store).verify(bad_card)
    assert not bad_result.ok
    assert any("MissingModule" in error for error in bad_result.errors)


def test_eval_runner_generates_json_and_markdown(registry, adapter, tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "case-1",
                "query": "AActor UPROPERTY ReplicatedUsing OnRep",
                "expected_files": ["Actor.h"],
                "expected_modules": ["Engine"],
                "expected_symbols": ["AActor"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(dataset)
    assert report.count == 1
    assert report.file_recall_at_5 == 1.0
    assert report.symbol_recall == 1.0
    json_path, md_path = write_reports(report, tmp_path / "reports")
    assert json.loads(json_path.read_text(encoding="utf-8"))["count"] == 1
    assert Path(md_path).read_text(encoding="utf-8").startswith("# Codalith Eval Report")


def test_source_locator_covers_seed_eval_dataset(registry, adapter):
    compiler = ContextCompiler(registry, adapter)
    dataset = Path(__file__).parents[1] / "eval" / "datasets" / "ue50.jsonl"
    report = EvalRunner(compiler).run(dataset)
    assert report.file_recall_at_5 >= 0.70
