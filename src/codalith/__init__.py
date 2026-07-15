"""Source context service over versioned code corpora."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("codalith")
except PackageNotFoundError:  # pragma: no cover - source-only import outside uv
    __version__ = "0+unknown"
