"""Target.cs extractor placeholder for v0 compatibility."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TargetDefinition:
    name: str
    target_type: str | None = None
