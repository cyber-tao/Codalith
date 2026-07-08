"""Small cache primitives used by Codalith tests and local demos."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CachedValue(Generic[T]):
    value: T
    expires_at: float

    def is_expired(self, now: float | None = None) -> bool:
        return (monotonic() if now is None else now) >= self.expires_at


def cache_value(value: T, *, ttl_seconds: float, now: float | None = None) -> CachedValue[T]:
    started = monotonic() if now is None else now
    return CachedValue(value=value, expires_at=started + ttl_seconds)
