"""Lossless JSON transport of the engine's effective settings for RC6 workers."""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from backtest_engine import BacktestConfig
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.models.instrument import InstrumentModel
from openpine_contracts import content_hash, decimal_string

# These are process-local handles/resources, not silently serializable settings.
_LOCAL_ONLY = frozenset({
    "runtime", "realtime_ticks", "realtime_tick_provider", "bar_magnifier_bars",
    "tradingview_reference_path", "output_dir",
})
_EXTRA = frozenset({"exchange", "market_type", "request_manifest"})
_HASH_FIELD = "effective_config_hash"


def _portable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _portable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("config mapping keys must be strings")
        return {key: _portable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_portable(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_portable(item) for item in value]
    if type(value) in (str, int, float, bool) or value is None:
        return value
    raise ValueError(f"config value cannot cross worker boundary: {type(value).__name__}")


def _hashable(value: Any) -> Any:
    if isinstance(value, float):
        return decimal_string(Decimal(repr(value)))
    if isinstance(value, dict):
        return {key: _hashable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_hashable(item) for item in value]
    return value


def effective_config_hash(settings: Mapping[str, Any]) -> str:
    return content_hash(
        {"schema_id": "openpine.rc6.engine_config.v1", "settings": _hashable(dict(settings))},
        schema_id="openpine.rc6.engine_config.v1",
    )


def serialize_engine_config(config: Any, semantic_profile: str) -> dict[str, Any]:
    """Use engine defaults only for missing/None values, never for zero/False."""
    defaults = BacktestConfig(
        symbol=config.symbol, timeframe=config.timeframe,
        start_time=config.start_time, end_time=config.end_time,
        semantic_profile=semantic_profile,
    )
    payload: dict[str, Any] = {}
    for field in dataclasses.fields(defaults):
        default = getattr(defaults, field.name)
        value = getattr(config, field.name, default)
        if value is None and default is not None:
            value = default
        if field.name in _LOCAL_ONLY:
            if value is not None:
                raise ValueError(f"RC6 worker does not transport {field.name}")
            continue
        payload[field.name] = _portable(value)
    # The native engine field takes precedence. Legacy adapters must resolve
    # their aliases before invoking the isolated runner; do not rename 'none'.
    if not hasattr(config, "qty_rounding") and hasattr(config, "qty_rounding_mode"):
        value = config.qty_rounding_mode
        if value is not None:
            payload["qty_rounding"] = value
    if payload["qty_rounding"] not in {"floor", "ceil", "nearest", "none", "truncate"}:
        raise ValueError("RC6 qty_rounding must be floor, ceil, nearest, none or truncate")
    payload["semantic_profile"] = semantic_profile
    for name in _EXTRA:
        value = getattr(config, name, None)
        if value is not None:
            payload[name] = _portable(value)
    # Copy nested containers and reject NaN/Infinity before spawning a worker.
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload[_HASH_FIELD] = effective_config_hash(payload)
    return payload


def resolve_engine_config(payload: Mapping[str, Any], context: Mapping[str, Any]) -> BacktestConfig:
    """Reconstruct once; the broker and delegated handler share this config."""
    if not isinstance(payload, Mapping):
        raise ValueError("RC6 engine config must be an object")
    values = json.loads(json.dumps(dict(payload), allow_nan=False))
    claimed_hash = values.pop(_HASH_FIELD, None)
    if claimed_hash is not None and claimed_hash != effective_config_hash(values):
        raise ValueError("effective config hash mismatch")
    fields = {field.name for field in dataclasses.fields(BacktestConfig)}
    unknown = set(values) - fields - _EXTRA
    if unknown:
        raise ValueError(f"unknown RC6 engine settings: {sorted(unknown)}")
    for name in _LOCAL_ONLY:
        if values.get(name) is not None:
            raise ValueError(f"RC6 worker does not transport {name}")
        values.pop(name, None)
    for name in ("symbol", "timeframe", "semantic_profile"):
        if name in context and values.get(name) != context[name]:
            raise ValueError(f"engine config {name} differs from execution context")
    extras = {name: values.pop(name) for name in _EXTRA if name in values}
    for name in ("required_outputs", "required_metrics"):
        if name in values:
            items = values[name]
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise ValueError(f"{name} must be an array of strings")
            values[name] = set(items)
    model = values.get("instrument_model")
    if model is not None:
        if not isinstance(model, dict):
            raise ValueError("instrument_model must be an object")
        values["instrument_model"] = InstrumentModel(**model)
    config = BacktestConfig(**values)
    if config.qty_rounding not in {"floor", "ceil", "nearest", "none", "truncate"}:
        raise ValueError("RC6 qty_rounding must be floor, ceil, nearest, none or truncate")
    validate_backtest_config(config)
    for name, value in extras.items():
        setattr(config, name, value)
    # This hashes the resolved settings, including engine-enforced output flags.
    resolved = serialize_engine_config(config, config.semantic_profile)
    config.effective_config_hash = resolved[_HASH_FIELD]
    return config
