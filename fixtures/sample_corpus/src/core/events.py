"""Event dispatch primitives used by the sample source corpus."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    payload: dict[str, object]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Event], None]]] = {}

    def subscribe(self, name: str, handler: Callable[[Event], None]) -> None:
        self._handlers.setdefault(name, []).append(handler)

    def dispatch(self, event: Event) -> None:
        for handler in self._handlers.get(event.name, []):
            handler(event)
