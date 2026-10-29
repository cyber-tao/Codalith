"""Generation-scoped resource sharing with deferred closing."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Lease(Generic[T]):
    resource: T
    users: int = 0
    retired: bool = False


class GenerationLeases(Generic[T]):
    """Share one resource per corpus generation and close superseded ones once idle."""

    def __init__(self, close: Callable[[T], None]) -> None:
        self._close = close
        self._lock = threading.Lock()
        self._leases: dict[tuple[str, str], _Lease[T]] = {}
        self._closed = False

    @contextmanager
    def lease(
        self,
        corpus_id: str,
        generation_id: str,
        open_resource: Callable[[], T],
    ) -> Iterator[T]:
        key = (corpus_id, generation_id)
        idle: list[T] = []
        with self._lock:
            if self._closed:
                raise RuntimeError("Generation resources are closed")
            entry = self._leases.get(key)
            if entry is None:
                entry = _Lease(open_resource())
                self._leases[key] = entry
            entry.users += 1
            entry.retired = False
            for other_key, other in list(self._leases.items()):
                if other_key[0] != corpus_id or other_key == key:
                    continue
                other.retired = True
                if other.users == 0:
                    del self._leases[other_key]
                    idle.append(other.resource)
        for resource in idle:
            self._close(resource)
        try:
            yield entry.resource
        finally:
            with self._lock:
                entry.users -= 1
                finished = (
                    entry.retired and entry.users == 0 and self._leases.get(key) is entry
                )
                if finished:
                    del self._leases[key]
            if finished:
                self._close(entry.resource)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            resources: list[T] = []
            for key, entry in list(self._leases.items()):
                entry.retired = True
                if entry.users == 0:
                    del self._leases[key]
                    resources.append(entry.resource)
        for resource in resources:
            self._close(resource)
