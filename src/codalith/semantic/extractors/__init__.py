"""Pluggable semantic extractor profile runner."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from codalith.errors import ConfigurationError

if TYPE_CHECKING:
    from codalith.semantic.store import SemanticStore

def get_profile(name: str | None) -> None:
    """Validate a semantic profile name.

    Codalith core does not ship domain-specific extractors. A corpus without a
    profile is a valid generic source corpus and produces an empty semantic
    summary.
    """
    if name is None:
        return None
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
    _ = (root, store, stop_after_min, min_modules, min_reflection_entities, min_guards)
    get_profile(name)
    return {
        "corpus_id": corpus_id,
        "profile": None,
        "source_files": 0,
        "modules": 0,
        "module_dependencies": 0,
        "reflection_entities": 0,
        "cpp_symbols": 0,
        "compile_guards": 0,
        "targets": 0,
        "plugins": 0,
        "projects": 0,
        "semantic_store": None,
    }
