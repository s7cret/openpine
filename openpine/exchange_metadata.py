"""Exchange instrument metadata helpers."""

from __future__ import annotations

import json
import os
import time
from importlib import import_module
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

BINANCE_SPOT_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_BINANCE_SPOT_CACHE_TTL_SEC = 24 * 60 * 60
_BINANCE_SPOT_INFO: dict | None = None
# Offline-safe deterministic fallbacks for common Binance spot symbols.
# API/cache metadata still wins when available. Values are LOT_SIZE stepSize.
_BINANCE_SPOT_STEP_FALLBACKS: dict[str, float] = {
    "BTCUSDT": 0.00001,
    "ETHUSDT": 0.0001,
    "BNBUSDT": 0.001,
    "SOLUSDT": 0.001,
    "XRPUSDT": 1.0,
    "ADAUSDT": 0.1,
    "DOGEUSDT": 1.0,
    "MATICUSDT": 0.1,
    "LTCUSDT": 0.001,
    "XLMUSDT": 1.0,
}


def marketdata_exchange_payloads() -> list[dict[str, object]]:
    registry = import_module("marketdata_provider.exchanges.registry")
    return registry.exchange_payloads(native_only=False)


def default_qty_step(exchange: str, market_type: str, symbol: str) -> float | None:
    if exchange.lower() != "binance" or market_type.lower() != "spot":
        return None
    normalized_symbol = symbol.upper()
    fallback = _BINANCE_SPOT_STEP_FALLBACKS.get(normalized_symbol)
    try:
        info = _load_binance_spot_exchange_info(fetch_network=fallback is None)
    except TypeError:
        info = _load_binance_spot_exchange_info()
    symbol_info = _symbol_info(info, normalized_symbol)
    if symbol_info is None:
        return fallback
    lot_size = _filter(symbol_info, "LOT_SIZE")
    if lot_size is None:
        return fallback
    return _float_or_none(lot_size.get("stepSize")) or fallback


def default_qty_rounding_mode(exchange: str, market_type: str, symbol: str) -> str:
    return (
        "truncate"
        if default_qty_step(exchange, market_type, symbol) is not None
        else "none"
    )


def _load_binance_spot_exchange_info(*, fetch_network: bool = False) -> dict | None:
    global _BINANCE_SPOT_INFO
    if _BINANCE_SPOT_INFO is not None:
        return _BINANCE_SPOT_INFO

    cache_path = _binance_spot_cache_path()
    cached = _read_fresh_cache(cache_path)
    if cached is not None:
        _BINANCE_SPOT_INFO = cached
        return cached
    if (
        not fetch_network
        and os.environ.get("OPENPINE_BINANCE_EXCHANGE_INFO_REFRESH") != "1"
    ):
        return _read_cache(cache_path)

    try:
        with urlopen(BINANCE_SPOT_EXCHANGE_INFO_URL, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        payload = _read_cache(cache_path)
    if payload is not None:
        _write_cache(cache_path, payload)
    _BINANCE_SPOT_INFO = payload
    return payload


def _binance_spot_cache_path() -> Path:
    configured = os.environ.get("OPENPINE_BINANCE_EXCHANGE_INFO_CACHE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "openpine" / "binance_spot_exchange_info.json"


def _read_fresh_cache(path: Path) -> dict | None:
    try:
        if time.time() - path.stat().st_mtime > _BINANCE_SPOT_CACHE_TTL_SEC:
            return None
    except OSError:
        return None
    return _read_cache(path)


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def _symbol_info(payload: dict | None, symbol: str) -> dict | None:
    for item in (payload or {}).get("symbols", []):
        if item.get("symbol") == symbol:
            return item
    return None


def _filter(symbol_info: dict, filter_type: str) -> dict | None:
    for item in symbol_info.get("filters", []):
        if item.get("filterType") == filter_type:
            return item
    return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
