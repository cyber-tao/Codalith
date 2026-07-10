from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codalith.coderag import CodeRAGAdapter
from codalith.compiler.context_compiler import ContextCompiler
from codalith.corpus.registry import CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.corpus.source_reader import SourceReader
from codalith.corpus.uri_resolver import URIResolver
from codalith.gateway.audit import AuditLogger
from codalith.gateway.auth import AuthContext
from codalith.gateway.tools import CodalithTools, ToolRuntime
from codalith.semantic.store import SemanticStore
from codalith.semantic.types import SourceSymbol


@pytest.fixture()
def sample_corpus_root(tmp_path: Path) -> Path:
    root = tmp_path / "sample"
    files = {
        "src/core/cache.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class CachedValue:\n"
            "    value: object\n"
            "    expires_at: float\n\n"
            "def cache_value(value, *, ttl_seconds, now):\n"
            "    return CachedValue(value, now + ttl_seconds)\n"
        ),
        "src/core/events.py": (
            "class EventBus:\n"
            "    def __init__(self):\n"
            "        self.handlers = {}\n\n"
            "    def subscribe(self, name, handler):\n"
            "        self.handlers.setdefault(name, []).append(handler)\n\n"
            "    def dispatch(self, event):\n"
            "        for handler in self.handlers.get(event.name, []):\n"
            "            handler(event)\n"
        ),
        "src/project/feature.py": (
            "from core.events import EventBus\n\n"
            "class ProjectFeature:\n"
            "    def __init__(self, bus: EventBus):\n"
            "        self.bus = bus\n"
        ),
        "generated/build.log": "EventBus generated diagnostics\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture()
def source_priors_path(tmp_path: Path) -> Path:
    path = tmp_path / "source_priors.json"
    path.write_text(
        json.dumps(
            {
                "identifier_stopwords": ["Sample"],
                "module_hints": ["core"],
                "priors": [
                    {
                        "path": "src/core/cache.py",
                        "title": "Cache API",
                        "module": "core",
                        "triggers": ["cache", "ttl", "CachedValue"],
                        "line_terms": ["CachedValue", "ttl"],
                    },
                    {
                        "path": "src/core/events.py",
                        "title": "Event Dispatch",
                        "module": "core",
                        "triggers": ["event", "dispatch", "subscribe", "EventBus"],
                        "line_terms": ["EventBus", "dispatch"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def seed_cards_path(tmp_path: Path) -> Path:
    path = tmp_path / "seed_cards.json"
    path.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "card_id": "module-core-cache",
                        "card_type": "module",
                        "title": "Core Cache API",
                        "path": "src/core/cache.py",
                        "related_node": "module:core",
                    },
                    {
                        "card_id": "module-core-events",
                        "card_type": "module",
                        "title": "Core Event Dispatch",
                        "path": "src/core/events.py",
                        "related_node": "module:core",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def registry_path(
    tmp_path: Path,
    sample_corpus_root: Path,
    source_priors_path: Path,
    seed_cards_path: Path,
) -> Path:
    path = tmp_path / "corpus_registry.json"
    data: dict[str, Any] = {
        "corpora": {
            "sample-codebase": {
                "kind": "source",
                "version": "sample",
                "display_name": "Sample Codebase",
                "description": "Neutral source corpus",
                "keywords": ["cache", "events"],
                "source_revision": "TEST",
                "source_root": str(sample_corpus_root),
                "indexed_root": str(sample_corpus_root),
                "coderag_store": str(tmp_path / "store"),
                "semantic_schema": "sample_codebase",
                "card_root": str(tmp_path / "cards"),
                "source_priors_path": str(source_priors_path),
                "seed_cards_path": str(seed_cards_path),
                "default": True,
                "access_scopes": ["source:read"],
                "scope_prefixes": {"source": ["src/"], "docs": ["docs/"]},
                "module_roots": ["src"],
                "index_ignore_dirs": ["build", "dist", "__pycache__"],
                "index_suffixes": [".py", ".md", ".json"],
            },
            "sample-next": {
                "kind": "source",
                "version": "sample-next",
                "source_revision": "TEST-NEXT",
                "source_root": str(sample_corpus_root),
                "indexed_root": str(sample_corpus_root),
                "coderag_store": str(tmp_path / "store-next"),
                "semantic_schema": "sample_next",
                "card_root": str(tmp_path / "cards-next"),
                "source_priors_path": str(source_priors_path),
                "default": False,
                "access_scopes": ["source:read"],
                "module_roots": ["src"],
                "index_suffixes": [".py"],
            },
        },
        "projects": {
            "SampleProject": {
                "kind": "project",
                "base_corpus": "sample-codebase",
                "source_root": str(sample_corpus_root),
                "indexed_root": str(sample_corpus_root),
                "coderag_store": str(tmp_path / "project-store"),
                "semantic_schema": "sample_project",
                "card_root": str(tmp_path / "project-cards"),
                "access_scopes": ["source:read"],
                "module_roots": ["src"],
                "index_suffixes": [".py"],
            }
        },
        "generated": {
            "generated-sample": {
                "kind": "generated",
                "base_corpus": "sample-codebase",
                "version": "sample",
                "source_root": str(sample_corpus_root),
                "indexed_root": str(sample_corpus_root),
                "coderag_store": str(tmp_path / "generated-store"),
                "semantic_schema": "generated_sample",
                "card_root": str(tmp_path / "generated-cards"),
                "access_scopes": ["generated:read", "source:read"],
            }
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def policy_path(tmp_path: Path) -> Path:
    path = tmp_path / "source_policy.json"
    path.write_text(
        json.dumps(
            {
                "limits": {
                    "default_max_lines": 20,
                    "hard_max_lines": 25,
                    "max_source_reads_per_10min": 100,
                    "max_total_lines_per_10min": 10000,
                },
                "deny_patterns": ["secrets/**"],
                "sensitive_patterns": [{"pattern": "vendor/**", "required_scope": "vendor:read"}],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def registry(registry_path: Path) -> CorpusRegistry:
    return CorpusRegistry.from_file(registry_path)


@pytest.fixture()
def adapter(registry: CorpusRegistry) -> CodeRAGAdapter:
    return CodeRAGAdapter(registry)


@pytest.fixture()
def tools(
    tmp_path: Path,
    registry: CorpusRegistry,
    policy_path: Path,
    adapter: CodeRAGAdapter,
) -> CodalithTools:
    resolver = URIResolver(registry)
    policy = SourcePolicy.from_file(str(policy_path))
    source_reader = SourceReader(registry)
    semantic_store = SemanticStore(tmp_path / "semantic.sqlite")
    corpus = registry.get_base()
    semantic_store.upsert_corpus(corpus)
    semantic_store.upsert_module(corpus_id=corpus.corpus_id, module_name="core", source_uri="codalith://sample-codebase/module/core")
    semantic_store.upsert_source_file(
        corpus_id=corpus.corpus_id,
        path="src/core/cache.py",
        language="python",
        line_count=10,
        module_name="core",
    )
    semantic_store.upsert_source_file(
        corpus_id=corpus.corpus_id,
        path="src/core/events.py",
        language="python",
        line_count=10,
        module_name="core",
    )
    semantic_store.upsert_symbol(
        corpus_id=corpus.corpus_id,
        path="src/core/cache.py",
        symbol=SourceSymbol(name="CachedValue", kind="class", line=4),
        evidence_uri="codalith://sample-codebase/source/src/core/cache.py#L1-L8",
        module_name="core",
    )
    semantic_store.upsert_symbol(
        corpus_id=corpus.corpus_id,
        path="src/core/events.py",
        symbol=SourceSymbol(name="EventBus", kind="class", line=1),
        evidence_uri="codalith://sample-codebase/source/src/core/events.py#L1-L10",
        module_name="core",
    )
    semantic_store.upsert_graph_edge(
        corpus_id=corpus.corpus_id,
        from_node="module:core",
        edge_type="uses",
        to_node="symbol:EventBus",
        evidence_uri="codalith://sample-codebase/source/src/core/events.py#L1-L10",
        extractor="manual",
    )
    compiler = ContextCompiler(
        registry,
        adapter,
        semantic_store=semantic_store,
        source_reader=source_reader,
    )
    runtime = ToolRuntime(
        registry=registry,
        resolver=resolver,
        policy=policy,
        source_reader=source_reader,
        adapter=adapter,
        compiler=compiler,
        audit=AuditLogger(tmp_path / "audit.jsonl"),
        identity=AuthContext(
            user_id="test-user",
            session_id="test-session",
            client="pytest",
            scopes=frozenset({"source:read", "index:status", "cards:read", "graph:read", "generated:read"}),
        ),
        semantic_store=semantic_store,
    )
    return CodalithTools(runtime)
