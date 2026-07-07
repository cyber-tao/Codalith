"""Small JSON config loader with environment placeholder expansion."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, cast

from codalith.errors import ConfigurationError

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigurationError(f"Config file does not exist: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{config_path} must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{config_path} must contain a mapping at the top level")
    return cast(dict[str, Any], _expand_env_placeholders(data, config_path=config_path, location="$"))


def _expand_env_placeholders(value: Any, *, config_path: Path, location: str) -> Any:
    if isinstance(value, str):
        return _expand_env_string(value, config_path=config_path, location=location)
    if isinstance(value, list):
        return [
            _expand_env_placeholders(item, config_path=config_path, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        expanded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigurationError(f"{config_path} contains a non-string key at {location}")
            expanded_key = _expand_env_string(key, config_path=config_path, location=f"{location}.<key>")
            if expanded_key in expanded:
                raise ConfigurationError(
                    f"{config_path} expands multiple keys to {expanded_key!r} at {location}"
                )
            expanded[expanded_key] = _expand_env_placeholders(
                item,
                config_path=config_path,
                location=f"{location}.{expanded_key}",
            )
        return expanded
    return value


def _expand_env_string(value: str, *, config_path: Path, location: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        if name in os.environ and (os.environ[name] or default is None):
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigurationError(
            f"{config_path} references unset environment variable {name!r} at {location}"
        )

    return _ENV_PLACEHOLDER.sub(replace, value)
