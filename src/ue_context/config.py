"""Small config loader for JSON-compatible YAML files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ue_context.errors import ConfigurationError


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigurationError(f"Config file does not exist: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"{config_path} must be JSON-compatible YAML for the v0 loader: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{config_path} must contain a mapping at the top level")
    return data
