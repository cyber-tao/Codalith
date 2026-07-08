"""Shared CLI helpers for Codalith jobs."""

from __future__ import annotations

import argparse

from codalith.cards.generator import attach_source_hashes, built_in_cards
from codalith.cards.schema import KnowledgeCard
from codalith.coderag import CodeRAGAdapter
from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.uri_resolver import URIResolver

DEFAULT_REGISTRY_PATH = "configs/corpus_registry.json"


def add_corpus_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--corpus", help="Explicit corpus id or version alias")
    parser.add_argument("--project", help="Project overlay corpus id")
    parser.add_argument(
        "--version", default=None, help="Corpus version (defaults to the registry default corpus)"
    )


def resolve_corpus(args: argparse.Namespace) -> tuple[CorpusRegistry, Corpus]:
    registry = CorpusRegistry.from_file(args.registry)
    if getattr(args, "corpus", None):
        return registry, registry.get_corpus(args.corpus)
    if getattr(args, "project", None):
        return registry, registry.get_project(args.project)
    return registry, registry.get_base(args.version)


def load_seed_cards(
    corpus: Corpus,
    resolver: URIResolver,
    adapter: CodeRAGAdapter,
) -> list[KnowledgeCard]:
    """Build the corpus seed cards with evidence hashes attached."""
    return attach_source_hashes(
        built_in_cards(
            corpus_id=corpus.corpus_id,
            version=corpus.version_label,
            seed_cards_path=corpus.seed_cards_path,
        ),
        resolver,
        adapter,
    )
