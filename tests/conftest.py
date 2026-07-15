from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from codalith.corpus.registry import Corpus, CorpusRegistry
from codalith.corpus.source_policy import SourcePolicy
from codalith.indexing.structure.builder import StructureBuilder
from codalith.query.service import QueryService


@dataclass(frozen=True)
class TestEnvironment:
    __test__ = False

    root: Path
    source_root: Path
    index_root: Path
    registry_path: Path
    policy_path: Path
    registry: CorpusRegistry
    policy: SourcePolicy
    corpus: Corpus

    def service(self) -> QueryService:
        return QueryService(self.registry, self.policy)


EnvironmentFactory = Callable[..., TestEnvironment]


def build_environment(
    root: Path,
    *,
    files: dict[str, str] | None = None,
    semantic: bool = True,
    corpus_id: str = "sample",
    revision: str = "test-v1",
    adapter: str = "python",
    include_extensions: tuple[str, ...] = (".py",),
) -> TestEnvironment:
    source_root = root / "source"
    index_root = root / "index"
    source_root.mkdir(parents=True)
    content = files or {
        "src/core/cache.py": (
            "from dataclasses import dataclass\n"
            "from time import monotonic\n\n"
            "@dataclass\n"
            "class CachedValue:\n"
            "    value: object\n"
            "    expires_at: float\n\n"
            "    def is_expired(self, now: float | None = None) -> bool:\n"
            "        return (monotonic() if now is None else now) >= self.expires_at\n\n"
            "def cache_value(value: object, ttl: float) -> CachedValue:\n"
            "    return CachedValue(value, monotonic() + ttl)\n"
        ),
        "src/core/events.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class Event:\n"
            "    name: str\n\n"
            "class EventBus:\n"
            "    def __init__(self) -> None:\n"
            "        self.handlers = {}\n\n"
            "    def dispatch(self, event: Event) -> None:\n"
            "        for handler in self.handlers.get(event.name, []):\n"
            "            handler(event)\n"
        ),
        "docs/architecture.md": "This document must never enter the source index.\n",
        ".env": "SECRET=must-not-be-readable\n",
    }
    for relative, text in content.items():
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    registry_path = root / "registry.toml"
    registry_path.write_text(
        "\n".join(
            (
                "schema_version = 2",
                f'default_target = "{corpus_id}"',
                "",
                "[[corpora]]",
                f'id = "{corpus_id}"',
                'display_name = "Test corpus"',
                'description = "Isolated test source"',
                f'revision = "{revision}"',
                f'source_root = "{source_root.as_posix()}"',
                f'index_root = "{index_root.as_posix()}"',
                f'adapter = "{adapter}"',
                'embedding_provider = "fake"',
                "include_extensions = ["
                + ", ".join(f'"{item}"' for item in include_extensions)
                + "]",
                'exclude_globs = [".git/**", ".venv/**", "**/__pycache__/**"]',
                "",
                "[[workspaces]]",
                'id = "all"',
                f'corpora = ["{corpus_id}"]',
                "",
            )
        ),
        encoding="utf-8",
    )
    policy_path = root / "source-policy.toml"
    policy_path.write_text(
        "\n".join(
            (
                "default_max_lines = 40",
                "hard_max_lines = 200",
                "max_file_bytes = 1000000",
                'deny_globs = ["**/.env", "**/.git/**", "**/secrets/**", "**/*_key.pem"]',
                "",
            )
        ),
        encoding="utf-8",
    )
    registry = CorpusRegistry.from_file(registry_path)
    policy = SourcePolicy.from_file(policy_path)
    corpus = registry.get_corpus(corpus_id)
    StructureBuilder(policy).build(
        corpus,
        semantic_mode="build" if semantic else "none",
    )
    return TestEnvironment(
        root,
        source_root,
        index_root,
        registry_path,
        policy_path,
        registry,
        policy,
        corpus,
    )


@pytest.fixture(scope="session")
def semantic_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestEnvironment]:
    environment = build_environment(tmp_path_factory.mktemp("semantic"), semantic=True)
    yield environment


@pytest.fixture
def environment_factory(tmp_path: Path) -> EnvironmentFactory:
    counter = 0

    def factory(**kwargs: object) -> TestEnvironment:
        nonlocal counter
        counter += 1
        return build_environment(tmp_path / f"environment-{counter}", **kwargs)  # type: ignore[arg-type]

    return factory
