"""Safe, typed msgpack codec for strategy resume state.

Only inert container tags and dataclasses from the installed backtest engine are
reconstructed.  Construction bypasses ``__init__``/``__post_init__`` so a
snapshot cannot select executable constructors.
"""

from __future__ import annotations

import dataclasses
import importlib
from collections import deque
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_MARKER = "__openpine_state_codec_v1__"
_ALLOWED_DATACLASS_PREFIXES = ("backtest_engine.",)


def to_msgpack_safe(value: Any) -> Any:
    """Convert resume state to msgpack primitives with explicit type tags."""

    if value is None or isinstance(value, str | bytes | int | float | bool):
        return value
    if isinstance(value, Enum):
        return {
            _MARKER: "enum",
            "type": f"{type(value).__module__}:{type(value).__qualname__}",
            "value": to_msgpack_safe(value.value),
        }
    if isinstance(value, Decimal):
        return {_MARKER: "decimal", "value": str(value)}
    if isinstance(value, Path):
        return {_MARKER: "path", "value": str(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        if not cls.__module__.startswith(_ALLOWED_DATACLASS_PREFIXES):
            raise TypeError(f"unsupported msgpack dataclass type: {cls.__module__}.{cls.__qualname__}")
        return {
            _MARKER: "dataclass",
            "type": f"{cls.__module__}:{cls.__qualname__}",
            "fields": {
                field.name: to_msgpack_safe(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, dict):
        if all(isinstance(key, str | bytes) for key in value):
            return {key: to_msgpack_safe(item) for key, item in value.items()}
        return {
            _MARKER: "mapping",
            "items": [
                [to_msgpack_safe(key), to_msgpack_safe(item)]
                for key, item in value.items()
            ],
        }
    if isinstance(value, list):
        return [to_msgpack_safe(item) for item in value]
    if isinstance(value, tuple):
        return {_MARKER: "tuple", "items": [to_msgpack_safe(item) for item in value]}
    if isinstance(value, set | frozenset):
        items = [to_msgpack_safe(item) for item in value]
        items.sort(key=repr)
        return {_MARKER: "frozenset" if isinstance(value, frozenset) else "set", "items": items}
    if isinstance(value, deque):
        return {
            _MARKER: "deque",
            "items": [to_msgpack_safe(item) for item in value],
            "maxlen": value.maxlen,
        }
    if isinstance(value, SimpleNamespace):
        # Preserve the long-standing public StateStore behavior for generic
        # namespaces: callers receive the exported mapping.
        return {key: to_msgpack_safe(item) for key, item in vars(value).items()}
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            scalar = item_method()
        except (TypeError, ValueError):
            pass
        else:
            if scalar is not value:
                return to_msgpack_safe(scalar)
    tolist_method = getattr(value, "tolist", None)
    if callable(tolist_method):
        try:
            return to_msgpack_safe(tolist_method())
        except (TypeError, ValueError):
            pass
    if callable(value):
        raise TypeError(f"unsupported msgpack value type: {type(value).__name__}")
    if hasattr(value, "__dict__"):
        return {
            key: to_msgpack_safe(item)
            for key, item in vars(value).items()
            if not key.startswith("_") and not callable(item)
        }
    raise TypeError(f"unsupported msgpack value type: {type(value).__name__}")


def _resolve_type(path: str) -> type[Any]:
    module_name, separator, qualname = path.partition(":")
    if not separator or not module_name.startswith(_ALLOWED_DATACLASS_PREFIXES):
        raise TypeError(f"unsafe snapshot type: {path}")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        if part.startswith("_"):
            raise TypeError(f"unsafe snapshot type: {path}")
        target = getattr(target, part)
    if not isinstance(target, type):
        raise TypeError(f"snapshot type is not a class: {path}")
    return target


def from_msgpack_safe(value: Any) -> Any:
    """Restore type-tagged resume state without pickle or constructors."""

    if isinstance(value, list):
        return [from_msgpack_safe(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get(_MARKER)
    if kind is None:
        return {key: from_msgpack_safe(item) for key, item in value.items()}
    if kind == "tuple":
        return tuple(from_msgpack_safe(item) for item in value["items"])
    if kind in {"set", "frozenset"}:
        restored = {from_msgpack_safe(item) for item in value["items"]}
        return frozenset(restored) if kind == "frozenset" else restored
    if kind == "deque":
        return deque(
            (from_msgpack_safe(item) for item in value["items"]),
            maxlen=value.get("maxlen"),
        )
    if kind == "mapping":
        return {
            from_msgpack_safe(key): from_msgpack_safe(item)
            for key, item in value["items"]
        }
    if kind == "decimal":
        return Decimal(value["value"])
    if kind == "path":
        return Path(value["value"])
    if kind == "enum":
        enum_type = _resolve_type(value["type"])
        if not issubclass(enum_type, Enum):
            raise TypeError(f"snapshot enum type is not an Enum: {value['type']}")
        return enum_type(from_msgpack_safe(value["value"]))
    if kind == "dataclass":
        cls = _resolve_type(value["type"])
        if not dataclasses.is_dataclass(cls):
            raise TypeError(f"snapshot type is not a dataclass: {value['type']}")
        fields = {field.name for field in dataclasses.fields(cls)}
        raw_fields = value.get("fields", {})
        if set(raw_fields) != fields:
            raise TypeError(f"snapshot dataclass fields do not match: {value['type']}")
        instance = object.__new__(cls)
        for name, item in raw_fields.items():
            object.__setattr__(instance, name, from_msgpack_safe(item))
        return instance
    raise TypeError(f"unknown snapshot codec tag: {kind}")
