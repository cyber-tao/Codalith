"""Stand-in so mypy can target Python 3.11 without parsing NumPy 2.5 stubs."""

from typing import Any

ndarray = Any
generic = Any
linalg: Any
random: Any

def __getattr__(name: str) -> Any: ...
