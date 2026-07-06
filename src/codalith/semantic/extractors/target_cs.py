"""Target.cs extractor for UnrealBuildTool target metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TargetDefinition:
    name: str
    target_type: str | None = None
    extra_modules: list[str] = field(default_factory=list)
    build_settings: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*TargetRules")
_TYPE_RE = re.compile(r"\bType\s*=\s*TargetType\.(?P<type>[A-Za-z_][A-Za-z0-9_]*)")
_BUILD_SETTINGS_RE = re.compile(
    r"\bDefaultBuildSettings\s*=\s*BuildSettingsVersion\.(?P<settings>[A-Za-z_][A-Za-z0-9_]*)"
)
_EXTRA_MODULES_RE = re.compile(
    r"\bExtraModuleNames\s*\.\s*Add(?:Range)?\s*\((?P<body>.*?)\)\s*;",
    re.DOTALL,
)
_STRING_RE = re.compile(r'"([^"]+)"')


def extract_target_text(text: str, *, fallback_name: str | None = None) -> TargetDefinition | None:
    clean = _strip_comments(text)
    class_match = _CLASS_RE.search(clean)
    name = class_match.group("name") if class_match else fallback_name
    if not name:
        return None
    type_match = _TYPE_RE.search(clean)
    settings_match = _BUILD_SETTINGS_RE.search(clean)
    modules: list[str] = []
    for match in _EXTRA_MODULES_RE.finditer(clean):
        modules.extend(_STRING_RE.findall(match.group("body")))
    return TargetDefinition(
        name=name.removesuffix("Target"),
        target_type=type_match.group("type") if type_match else None,
        extra_modules=list(dict.fromkeys(modules)),
        build_settings=settings_match.group("settings") if settings_match else None,
    )


def extract_target_file(path: str | Path) -> TargetDefinition | None:
    file_path = Path(path)
    fallback_name = file_path.name.removesuffix(".Target.cs")
    return extract_target_text(file_path.read_text(encoding="utf-8"), fallback_name=fallback_name)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
