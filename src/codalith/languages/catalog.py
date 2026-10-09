"""Dependency-free retrieval conventions contributed by language adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    """Naming and layout conventions the query layer may use for ranking."""

    type_prefixes: str = ""
    stop_words: frozenset[str] = frozenset()
    generic_file_names: frozenset[str] = frozenset()
    rules_file_suffixes: tuple[str, ...] = ()
    declaration_suffixes: tuple[str, ...] = ()
    public_dirs: tuple[str, ...] = ()
    private_dirs: tuple[str, ...] = ()


NEUTRAL_PROFILE = RetrievalProfile()

UNREAL_PROFILE = RetrievalProfile(
    type_prefixes="AUFSTIE",
    stop_words=frozenset({"unreal", "engine", "runtime"}),
    generic_file_names=frozenset({"build.cs", "generated.h", "target.cs"}),
    rules_file_suffixes=(".build.cs", ".target.cs"),
    declaration_suffixes=(".h", ".hh", ".hpp", ".hxx", ".inl"),
    public_dirs=("Public", "Classes"),
    private_dirs=("Private",),
)

_PROFILES = {"cpp-ue": UNREAL_PROFILE}


def retrieval_profile(adapter_id: str) -> RetrievalProfile:
    return _PROFILES.get(adapter_id, NEUTRAL_PROFILE)
