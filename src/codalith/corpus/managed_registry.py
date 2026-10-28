"""Combine static directory corpora with an authoritative managed snapshot catalog."""

from __future__ import annotations

from collections.abc import Callable

from codalith.corpus.registry import Corpus, CorpusRegistry, Target
from codalith.errors import ConfigurationError


class ManagedRegistry(CorpusRegistry):
    def __init__(self, registry: CorpusRegistry, snapshots: Callable[[], dict[str, Corpus]]) -> None:
        self._static = dict(registry.corpora)
        self._snapshots = snapshots
        super().__init__(path=registry.path, default_target=registry.default_target,
                         corpora=dict(self._static), workspaces=registry.workspaces,
                         allow_empty=True)
        self.refresh()

    def refresh(self) -> None:
        managed = self._snapshots()
        overlap = set(managed) & (set(self._static) | set(self.workspaces))
        if overlap:
            raise ConfigurationError("Managed target collides with a static target: " + ", ".join(sorted(overlap)))
        self.corpora = {**self._static, **managed}

    def resolve(self, target: str | None = None) -> Target:
        self.refresh()
        return super().resolve(target)

    def get_corpus(self, corpus_id: str) -> Corpus:
        self.refresh()
        return super().get_corpus(corpus_id)
