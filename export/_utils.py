"""Internal helpers shared by export writers."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def object_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    if isinstance(value, dict):
        return value
    return {}


def first(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None:
            return value
    return None


def int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
