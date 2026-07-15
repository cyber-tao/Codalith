"""TOML configuration loading with explicit environment expansion."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, cast

from codalith.errors import ConfigurationError

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def load_toml(path: str | Path) -> tuple[Path, dict[str, Any]]:
    """Load TOML and expand ``${VAR}`` / ``${VAR:-default}`` placeholders."""

    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {config_path}")
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Invalid TOML configuration {config_path}: {exc}") from exc
    expanded = _expand(payload, path=config_path, location="$")
    if not isinstance(expanded, dict):  # pragma: no cover - tomllib always returns a dict
        raise ConfigurationError(f"{config_path} must contain a top-level table")
    return config_path, cast(dict[str, Any], expanded)


def _expand(value: Any, *, path: Path, location: str) -> Any:
    if isinstance(value, str):
        return _expand_string(value, path=path, location=location)
    if isinstance(value, list):
        return [
            _expand(item, path=path, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _expand(item, path=path, location=f"{location}.{key}")
            for key, item in value.items()
        }
    return value


def _expand_string(value: str, *, path: Path, location: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        configured = os.getenv(name)
        if configured is not None and (configured != "" or default is None):
            return configured
        if default is not None:
            return default
        raise ConfigurationError(
            f"{path} references unset environment variable {name!r} at {location}"
        )

    return _ENV_PLACEHOLDER.sub(replacement, value)


def resolve_config_path(config_file: Path, raw: str) -> Path:
    """Resolve a path relative to the file that declares it, never process cwd."""

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = config_file.parent / candidate
    return candidate.resolve()
