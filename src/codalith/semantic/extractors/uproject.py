"""UProject metadata extractor."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codalith.semantic.extractors.uplugin import PluginModule


@dataclass(frozen=True, slots=True)
class ProjectDescriptor:
    name: str
    path: str
    modules: list[PluginModule]
    plugins: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_uproject(path: str | Path) -> dict[str, Any]:
    data = json.loads(_strip_json_comments(Path(path).read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        raise ValueError(f"UProject must be a JSON object: {path}")
    return data


def _strip_json_comments(text: str) -> str:
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def extract_uproject(path: str | Path, *, root: Path | None = None) -> ProjectDescriptor:
    file_path = Path(path)
    data = load_uproject(file_path)
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
    plugins: dict[str, bool] = {}
    for item in data.get("Plugins", []):
        if isinstance(item, dict) and item.get("Name"):
            plugins[str(item["Name"])] = bool(item.get("Enabled", True))
    relative = file_path.relative_to(root).as_posix() if root and file_path.is_relative_to(root) else file_path.as_posix()
    return ProjectDescriptor(
        name=file_path.stem,
        path=relative,
        modules=modules,
        plugins=plugins,
        metadata={"engine_association": data.get("EngineAssociation")},
    )
