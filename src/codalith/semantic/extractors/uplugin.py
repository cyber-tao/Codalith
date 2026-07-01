"""UPlugin metadata extractor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_uplugin(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"UPlugin must be a JSON object: {path}")
    return data
