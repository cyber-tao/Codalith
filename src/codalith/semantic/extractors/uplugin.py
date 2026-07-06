"""UPlugin metadata extractor."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PluginModule:
    name: str
    module_type: str | None = None
    loading_phase: str | None = None
    supported_platforms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    name: str
    path: str
    modules: list[PluginModule]
    supported_platforms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_uplugin(path: str | Path) -> dict[str, Any]:
    data = json.loads(_strip_json_comments(Path(path).read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        raise ValueError(f"UPlugin must be a JSON object: {path}")
    return data


def _strip_json_comments(text: str) -> str:
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def extract_uplugin(path: str | Path, *, root: Path | None = None) -> PluginDescriptor:
    file_path = Path(path)
    data = load_uplugin(file_path)
    modules: list[PluginModule] = []
    for item in data.get("Modules", []):
        if not isinstance(item, dict) or not item.get("Name"):
            continue
        modules.append(
            PluginModule(
                name=str(item["Name"]),
                module_type=str(item["Type"]) if item.get("Type") is not None else None,
                loading_phase=str(item["LoadingPhase"]) if item.get("LoadingPhase") is not None else None,
                supported_platforms=[str(value) for value in item.get("WhitelistPlatforms", []) or item.get("PlatformAllowList", [])],
            )
        )
    relative = file_path.relative_to(root).as_posix() if root and file_path.is_relative_to(root) else file_path.as_posix()
    return PluginDescriptor(
        name=file_path.stem,
        path=relative,
        modules=modules,
        supported_platforms=[str(value) for value in data.get("SupportedTargetPlatforms", [])],
        metadata={
            "friendly_name": data.get("FriendlyName"),
            "version_name": data.get("VersionName"),
            "category": data.get("Category"),
        },
    )
