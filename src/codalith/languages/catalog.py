"""Dependency-free adapter metadata shared by configuration, indexing, and queries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


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


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    """Raising ``version`` invalidates every generation built by the adapter."""

    adapter_id: str
    version: int
    profile: RetrievalProfile = NEUTRAL_PROFILE


ADAPTERS = {
    spec.adapter_id: spec
    for spec in (
        AdapterSpec("python", 3),
        AdapterSpec("csharp", 2),
        AdapterSpec("cpp-ue", 7, UNREAL_PROFILE),
        AdapterSpec("generic", 1),
    )
}

_LANGUAGES_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".cs": "csharp",
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".inl": "cpp",
    ".ispc": "ispc",
    ".m": "objective-c",
    ".mm": "objective-cpp",
    ".usf": "hlsl",
    ".ush": "hlsl",
}


def retrieval_profile(adapter_id: str) -> RetrievalProfile:
    return ADAPTERS[adapter_id].profile


def language_for_path(path: str) -> str:
    return _LANGUAGES_BY_SUFFIX.get(PurePosixPath(path).suffix.lower(), "text")
