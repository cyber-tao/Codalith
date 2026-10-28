"""Explicit, local management configuration; credentials stay in the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codalith.config import load_toml, resolve_config_path
from codalith.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class ManagementConfig:
    enabled: bool = False
    storage_root: Path = Path("data/management")
    source_roots: tuple[Path, ...] = ()
    admin_token: str = field(default="", repr=False)
    max_selected_tags: int = 100
    profiles: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_registry(cls, path: str | Path) -> ManagementConfig:
        config_path, payload = load_toml(path)
        raw = payload.get("management", {})
        if not isinstance(raw, dict):
            raise ConfigurationError("management must be a TOML table")
        allowed = {
            "enabled",
            "storage_root",
            "source_roots",
            "admin_token_env",
            "max_selected_tags",
            "profiles",
        }
        if set(raw) - allowed:
            raise ConfigurationError("Unknown management configuration field")
        enabled_raw = os.getenv("CODALITH_MANAGEMENT_ENABLED")
        enabled = raw.get("enabled", False)
        if enabled_raw is not None:
            if enabled_raw.lower() not in {"true", "false", "1", "0"}:
                raise ConfigurationError("CODALITH_MANAGEMENT_ENABLED must be true or false")
            enabled = enabled_raw.lower() in {"true", "1"}
        if not isinstance(enabled, bool):
            raise ConfigurationError("management.enabled must be boolean")
        storage = os.getenv(
            "CODALITH_MANAGEMENT_STORAGE_ROOT", raw.get("storage_root", "../data/management")
        )
        roots = raw.get("source_roots", [])
        roots_env = os.getenv("CODALITH_MANAGEMENT_SOURCE_ROOTS")
        if roots_env is not None:
            roots = roots_env.split(os.pathsep) if roots_env else []
        if (
            not isinstance(storage, str)
            or not isinstance(roots, list)
            or any(not isinstance(item, str) for item in roots)
        ):
            raise ConfigurationError("Management storage and source roots must be paths")
        token_env = raw.get("admin_token_env", "CODALITH_ADMIN_TOKEN")
        if not isinstance(token_env, str) or not token_env:
            raise ConfigurationError("management.admin_token_env must name an environment variable")
        token = os.getenv(token_env, "")
        maximum = raw.get("max_selected_tags", 100)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 1000:
            raise ConfigurationError("management.max_selected_tags must be between 1 and 1000")
        if enabled and (len(token) < 16 or not roots):
            raise ConfigurationError(
                "Enabled management requires source_roots and an admin token of at least 16 characters"
            )
        profiles = raw.get("profiles", [])
        if not isinstance(profiles, list) or any(not isinstance(item, dict) for item in profiles):
            raise ConfigurationError("management.profiles must be an array of tables")
        return cls(
            enabled,
            resolve_config_path(config_path, storage),
            tuple(resolve_config_path(config_path, item) for item in roots),
            token,
            maximum,
            tuple(profiles),
        )
