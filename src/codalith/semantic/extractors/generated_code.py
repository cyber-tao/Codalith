"""Generated-code relation helpers."""

from __future__ import annotations

from pathlib import PurePosixPath


def generated_header_for(header_path: str) -> str:
    path = PurePosixPath(header_path)
    return path.with_name(f"{path.stem}.generated.h").as_posix()
