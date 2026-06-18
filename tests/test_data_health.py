from __future__ import annotations

import asyncio
from typing import cast
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from openpine.gateway.routes import accounts_data


def test_data_health_summarizes_catalog_settings_and_cached_series() -> None:
    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_timeframes=("1m", "3m", "1h"),
            marketdata_default_timeframe="3m",
            marketdata_stable_quotes_only=True,
            marketdata_stable_quote_assets=("USDT", "USDC"),
        )
    )
    summary = {
        "series": [
            {"exchange": "binance", "market_type": "spot", "symbol": "BTCUSDT", "timeframe": "1m", "status": "actual"},
            {"exchange": "binance", "market_type": "futures", "symbol": "BTCUSDT", "timeframe": "1h", "status": "stale"},
            {"exchange": "okx", "market_type": "futures", "symbol": "BTC-USDT-SWAP", "timeframe": "3m", "status": "actual"},
            {"symbol": "LEGACY", "timeframe": "1m", "status": "actual"},
        ]
    }

    payload = accounts_data._data_health_payload(state, summary=summary)

    assert payload["settings"]["default_timeframe"] == "3m"
    assert payload["settings"]["stable_quotes_only"] is True
    assert payload["totals"] == {
        "exchanges": 10,
        "enabled_exchanges": 10,
        "market_types": 35,
        "cached_series": 4,
        "cached_exchanges": 2,
        "cached_markets": 3,
        "actual_series": 3,
        "stale_series": 1,
        "unknown_series": 1,
    }
    binance = next(item for item in payload["exchanges"] if item["id"] == "binance")
    spot = next(item for item in binance["markets"] if item["id"] == "spot")
    assert spot["status"] == "actual"
    assert spot["cached_series"] == 1
    assert spot["symbols"] == ["BTCUSDT"]
    assert spot["timeframes"] == ["1m"]

    okx = next(item for item in payload["exchanges"] if item["id"] == "okx")
    futures = next(item for item in okx["markets"] if item["id"] == "futures")
    assert futures["status"] == "actual"
    assert futures["symbols"] == ["BTC-USDT-SWAP"]

    coinbase = next(item for item in payload["exchanges"] if item["id"] == "coinbase")
    assert coinbase["status"] == "available"
    assert coinbase["cached_series"] == 0


def test_data_health_endpoint_returns_runtime_matrix(monkeypatch) -> None:
    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_timeframes=("1m",),
            marketdata_default_timeframe="1m",
            marketdata_stable_quotes_only=False,
            marketdata_stable_quote_assets=("USDT",),
        )
    )
    monkeypatch.setattr(accounts_data, "_data_summary", lambda _state: {"series": []})

    payload = accounts_data.data_health(state)

    assert payload["source"] == "marketdata_provider.exchanges.registry + openpine.cache"
    assert payload["settings"]["stable_quotes_only"] is False
    assert len(payload["exchanges"]) == 10


def test_data_summary_endpoint_offloads_blocking_inventory(monkeypatch) -> None:
    state = SimpleNamespace()
    calls = []

    def fake_summary(received_state):
        return {"series": [], "orders": {}}

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(accounts_data, "_data_summary_cached", fake_summary)
    monkeypatch.setattr(accounts_data.asyncio, "to_thread", fake_to_thread)

    payload = asyncio.run(accounts_data.data_summary(cast(accounts_data.GatewayState, state)))

    assert payload == {"series": [], "orders": {}}
    assert calls == [(fake_summary, (state,), {})]


def test_data_cache_status_offloads_blocking_inventory(monkeypatch) -> None:
    state = SimpleNamespace()
    calls = []

    def fake_summary(received_state):
        return {
            "cache_size_bytes": 12,
            "series": [{"symbol": "BTCUSDT", "timeframe": "1m"}],
        }

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(accounts_data, "_data_summary_cached", fake_summary)
    monkeypatch.setattr(accounts_data.asyncio, "to_thread", fake_to_thread)

    payload = asyncio.run(accounts_data.data_cache_status(cast(accounts_data.GatewayState, state)))

    assert payload.total_size_bytes == 12
    assert payload.instruments == ["BTCUSDT"]
    assert payload.timeframes == ["1m"]
    assert calls == [(fake_summary, (state,), {})]


def test_data_health_handles_disabled_and_cached_only_markets(monkeypatch) -> None:
    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_timeframes=("1m",),
            marketdata_default_timeframe="1m",
            marketdata_stable_quotes_only=True,
            marketdata_stable_quote_assets=("USDT",),
        )
    )
    monkeypatch.setattr(
        accounts_data,
        "_market_metadata_payload",
        lambda: {
            "exchanges": [
                {
                    "id": "disabledex",
                    "name": "DisabledEx",
                    "rank": 1,
                    "openpine_enabled": False,
                    "market_types": [{"id": "spot", "label": "Spot"}],
                },
                {
                    "id": "cachex",
                    "name": "CacheEx",
                    "rank": 2,
                    "openpine_enabled": True,
                    "market_types": [{"id": "spot", "label": "Spot"}],
                },
            ]
        },
    )

    payload = accounts_data._data_health_payload(
        state,
        summary={"series": [{"exchange": "cachex", "market_type": "spot", "symbol": "ETHUSDT", "timeframe": "1m", "status": "cached"}]},
    )

    disabled = payload["exchanges"][0]
    cached = payload["exchanges"][1]
    assert payload["totals"]["enabled_exchanges"] == 1
    assert disabled["status"] == "disabled"
    assert disabled["markets"][0]["status"] == "disabled"
    assert cached["status"] == "cached"
    assert cached["markets"][0]["status"] == "cached"


def test_data_ticker24h_reports_timeout(monkeypatch) -> None:
    async def fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(accounts_data.asyncio, "wait_for", fake_wait_for)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(accounts_data.data_ticker24h("BTCUSDT", state=SimpleNamespace()))

    assert excinfo.value.status_code == 504
