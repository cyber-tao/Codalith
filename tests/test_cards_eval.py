from __future__ import annotations

import json
from pathlib import Path

from codalith.cards.generator import attach_source_hashes, built_in_cards, write_cards
from codalith.cards.parser import parse_card_markdown
from codalith.cards.repository import FileCardRepository
from codalith.cards.schema import CardClaim, KnowledgeCard
from codalith.cards.verifier import KnowledgeCardVerifier
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.uri_resolver import URIResolver
from codalith.eval.runner import EvalRunner, write_reports
from codalith.eval.runner import main as eval_main
from codalith.semantic.store import SemanticStore


def test_built_in_cards_verify_against_sample_fixture(registry, adapter, tmp_path):
    corpus = registry.get_base()
    resolver = URIResolver(registry)
    verifier = KnowledgeCardVerifier(resolver, adapter)
    cards = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        ),
        resolver,
        adapter,
    )

    assert len(cards) == 2
    assert all(card.source_hashes for card in cards)
    results = [verifier.verify(card) for card in cards]
    assert all(result.ok for result in results), [result.errors for result in results if not result.ok]
    written = write_cards(
        [
            result.verified_card(card)
            for card, result in zip(cards, results, strict=True)
        ],
        tmp_path,
    )
    assert len(written) == 2
    assert all(path.exists() for path in written)
    assert parse_card_markdown(written[0].read_text(encoding="utf-8")) == results[
        0
    ].verified_card(cards[0])


def test_written_cards_are_searchable_from_card_repository(registry):
    corpus = registry.get_base()
    cards = [
        card.with_verification("evidence_verified")
        for card in built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        )
    ]
    write_cards(cards, corpus.card_root)

    matches = FileCardRepository(registry).search(
        [corpus.corpus_id],
        "Core Cache API seed knowledge card",
    )

    assert matches
    assert matches[0].document.card.card_id == "module-core-cache"


def test_context_pack_only_uses_evidence_verified_cards(registry, adapter):
    corpus = registry.get_base()
    cards = built_in_cards(
        corpus_id=corpus.corpus_id,
        version=corpus.version_label,
        seed_cards_path=corpus.seed_cards_path,
    )
    write_cards(
        [cards[0].with_verification("evidence_verified"), cards[1]],
        corpus.card_root,
    )
    compiler = ContextCompiler(registry, adapter)

    pack = compiler.compile(query="Core Cache API seed knowledge card", corpus="sample")

    statuses = {card["uri"]: card["verification_status"] for card in pack.cards}
    assert statuses, "expected at least one card hit"
    assert set(statuses.values()) == {"evidence_verified"}
    assert all("module-core-cache" in uri for uri in statuses)


def test_card_evidence_keeps_reserved_context_budget(registry, adapter):
    corpus = registry.get_base()
    resolver = URIResolver(registry)
    card = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        )[:1],
        resolver,
        adapter,
    )[0].with_verification("evidence_verified")
    write_cards([card], corpus.card_root)

    pack = ContextCompiler(registry, adapter).compile(
        query="Core Cache API",
        corpus="sample",
        max_source_spans=2,
    )

    assert len(pack.source_spans) == 2
    assert any(span["source"] == "card-evidence" for span in pack.source_spans)


def test_card_without_evidence_fails(registry, adapter):
    card = KnowledgeCard(
        corpus_id="sample-codebase",
        card_id="bad",
        card_type="mechanism",
        title="Bad",
        version="sample",
        body_markdown="No evidence.",
        claims=[CardClaim(text="Unsupported claim", evidence=[])],
    )
    result = KnowledgeCardVerifier(URIResolver(registry), adapter).verify(card)
    assert not result.ok
    assert result.errors


def test_card_hash_mismatch_fails(registry, adapter):
    corpus = registry.get_base()
    resolver = URIResolver(registry)
    card = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        )[:1],
        resolver,
        adapter,
    )[0]
    bad_card = KnowledgeCard.from_dict(
        {
            **card.as_dict(),
            "source_hashes": {uri: "0" * 64 for uri in card.source_hashes},
        }
    )

    result = KnowledgeCardVerifier(resolver, adapter).verify(bad_card)

    assert not result.ok
    assert any("hash mismatch" in error for error in result.errors)


def test_card_verifier_checks_related_semantic_nodes(registry, adapter, tmp_path):
    corpus = registry.get_base()
    store = SemanticStore(tmp_path / "semantic.sqlite")
    store.upsert_module(corpus_id=corpus.corpus_id, module_name="core")
    # Mark the card evidence file as semantically scanned so related-node
    # existence is enforced rather than skipped.
    store.upsert_source_file(
        corpus_id=corpus.corpus_id,
        path="src/core/cache.py",
        language="python",
        line_count=10,
        module_name="core",
    )
    resolver = URIResolver(registry)
    card = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        )[:1],
        resolver,
        adapter,
    )[0]

    result = KnowledgeCardVerifier(resolver, adapter, store).verify(card)

    assert result.ok

    bad_card = KnowledgeCard.from_dict({**card.as_dict(), "related_nodes": ["module:MissingModule"]})
    bad_result = KnowledgeCardVerifier(resolver, adapter, store).verify(bad_card)
    assert not bad_result.ok
    assert any("MissingModule" in error for error in bad_result.errors)


def test_card_verifier_skips_related_nodes_when_semantic_db_is_unpopulated(
    registry, adapter, tmp_path
):
    corpus = registry.get_base()
    store = SemanticStore(tmp_path / "semantic.sqlite")
    resolver = URIResolver(registry)
    card = attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        )[:1],
        resolver,
        adapter,
    )[0]

    # No extractor has populated the store, so related nodes cannot be
    # asserted and verification must still pass.
    result = KnowledgeCardVerifier(resolver, adapter, store).verify(card)

    assert result.ok, result.errors


def test_eval_runner_generates_json_and_markdown(registry, adapter, tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "case-1",
                "query": "How does CachedValue handle ttl expiration?",
                "version": "sample",
                "expected_files": ["src/core/cache.py"],
                "expected_modules": ["core"],
                "expected_symbols": ["CachedValue"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    compiler = ContextCompiler(registry, adapter)
    report = EvalRunner(compiler).run(dataset)
    assert report.count == 1
    assert report.file_recall_at_k == 1.0
    assert report.symbol_recall == 1.0
    json_path, md_path = write_reports(report, tmp_path / "reports")
    assert json.loads(json_path.read_text(encoding="utf-8"))["count"] == 1
    assert Path(md_path).read_text(encoding="utf-8").startswith("# Codalith Eval Report")


def test_eval_cli_require_pass_returns_nonzero(registry_path, tmp_path):
    dataset = tmp_path / "failing.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "failure",
                "query": "No matching source",
                "version": "sample",
                "expected_files": ["src/core/does-not-exist.py"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert (
        eval_main(
            [
                "--registry",
                str(registry_path),
                "--dataset",
                str(dataset),
                "--output-dir",
                str(tmp_path / "reports"),
                "--require-pass",
            ]
        )
        == 1
    )
