"""Archive a CodeRAG LanceDB store after indexing completes."""

from __future__ import annotations

import argparse
import os
import tarfile
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--store-dir",
        default=os.getenv("CODERAG_STORE_DIR") or os.getenv("CODALITH_CODERAG_STORE_DIR"),
        help="CodeRAG store directory to archive.",
    )
    parser.add_argument("--output-dir", default="reports/backups")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if not args.store_dir:
        parser.error("--store-dir or CODERAG_STORE_DIR is required")
    store_dir = Path(args.store_dir)
    if not store_dir.is_dir():
        raise FileNotFoundError(f"CodeRAG store directory does not exist: {store_dir}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.label}" if args.label else ""
    archive = output_dir / f"{store_dir.name}{suffix}-{time.strftime('%Y%m%d%H%M%S')}.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(store_dir, arcname=store_dir.name)
    print(str(archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
