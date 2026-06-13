"""Small row/record helper functions shared by data adapters."""

from __future__ import annotations

from typing import Any, Iterable


def attr_or_item(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"missing any of: {', '.join(names)}")


def has_field(obj: Any, name: str) -> bool:
    return (isinstance(obj, dict) and name in obj) or hasattr(obj, name)


def has_any_field(obj: Any, names: tuple[str, ...]) -> bool:
    return any(has_field(obj, name) for name in names)


def duplicate_timestamps(bars: Iterable[Any]) -> tuple[int, ...]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for bar in bars:
        timestamp = int(attr_or_item(bar, "time"))
        if timestamp in seen:
            duplicates.add(timestamp)
        seen.add(timestamp)
    return tuple(sorted(duplicates))
