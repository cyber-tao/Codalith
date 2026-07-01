"""Build.cs dependency extractor v0."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModuleDependency:
    from_module: str
    to_module: str
    dep_kind: str
    metadata: dict[str, str] = field(default_factory=dict)


class BuildCsExtractor:
    _DEP_RE = re.compile(
        r"(?P<kind>PublicDependencyModuleNames|PrivateDependencyModuleNames|DynamicallyLoadedModuleNames)"
        r"\s*\.\s*Add(?:Range)?\s*\((?P<body>.*?)\)\s*;",
        re.DOTALL,
    )
    _STRING_RE = re.compile(r'"([^"]+)"')

    def extract_text(self, text: str, *, module_name: str) -> list[ModuleDependency]:
        deps: list[ModuleDependency] = []
        clean = _strip_comments(text)
        for match in self._DEP_RE.finditer(clean):
            dep_kind = _kind(match.group("kind"))
            for dependency in self._STRING_RE.findall(match.group("body")):
                deps.append(
                    ModuleDependency(
                        from_module=module_name,
                        to_module=dependency,
                        dep_kind=dep_kind,
                    )
                )
        return _dedupe(deps)

    def extract_file(self, path: str | Path) -> list[ModuleDependency]:
        file_path = Path(path)
        module_name = file_path.name.removesuffix(".Build.cs")
        return self.extract_text(file_path.read_text(encoding="utf-8"), module_name=module_name)


class ModuleDepStore(Protocol):
    def upsert_module_dep(
        self,
        *,
        corpus_id: str,
        dependency: ModuleDependency,
        evidence_uri: str,
    ) -> None: ...


def write_module_deps(
    store: ModuleDepStore,
    *,
    corpus_id: str,
    evidence_uri: str,
    dependencies: Iterable[ModuleDependency],
) -> None:
    for dependency in dependencies:
        store.upsert_module_dep(
            corpus_id=corpus_id,
            dependency=dependency,
            evidence_uri=evidence_uri,
        )


def _kind(raw: str) -> str:
    return {
        "PublicDependencyModuleNames": "public",
        "PrivateDependencyModuleNames": "private",
        "DynamicallyLoadedModuleNames": "dynamic",
    }[raw]


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", text, flags=re.MULTILINE)


def _dedupe(deps: list[ModuleDependency]) -> list[ModuleDependency]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ModuleDependency] = []
    for dep in deps:
        key = (dep.from_module, dep.to_module, dep.dep_kind)
        if key not in seen:
            out.append(dep)
            seen.add(key)
    return out
