"""Pluggable domain extractor profiles for the semantic graph.

A profile owns every domain-specific convention needed to extract semantic
data from a corpus tree (artifact types, scan roots, module inference). Which
profile a corpus uses is registry configuration (``semantic_profile``), never
a code-level assumption.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codalith.errors import ConfigurationError

if TYPE_CHECKING:
    from codalith.semantic.store import SemanticStore

SemanticProfile = Callable[..., dict[str, Any]]


def get_profile(name: str | None) -> SemanticProfile:
    """Resolve a semantic profile name to its extraction entry point."""
    if name == "unreal":
        from codalith.semantic.extractors.unreal import extract_semantic_summary

        return extract_semantic_summary
    if name is None:
        raise ConfigurationError(
            "Corpus does not define a semantic_profile; set it in the corpus registry"
        )
    raise ConfigurationError(f"Unknown semantic profile: {name}")


def run_profile(
    name: str | None,
    root: Path,
    *,
    corpus_id: str,
    store: SemanticStore | None = None,
    stop_after_min: bool = False,
    min_modules: int = 0,
    min_reflection_entities: int = 0,
    min_guards: int = 0,
) -> dict[str, Any]:
    profile = get_profile(name)
    return profile(
        root,
        corpus_id=corpus_id,
        store=store,
        stop_after_min=stop_after_min,
        min_modules=min_modules,
        min_reflection_entities=min_reflection_entities,
        min_guards=min_guards,
    )
