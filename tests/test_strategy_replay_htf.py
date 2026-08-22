from __future__ import annotations

import asyncio
import inspect
import sys
import types
from types import SimpleNamespace

from openpine.gateway.routes import strategies
from openpine.gateway.schemas import ReplayRequest
from openpine.gateway.routes.strategies import (
    _confirmed_htf_bars_for_replay,
    _run_isolated_strategy_replay,
)
from openpine.runtime.isolated_run import _confirmed_htf_bars_from_provider_bars


def test_isolated_strategy_replay_forwards_confirmed_htf_bars() -> None:
    seen: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True, bars_processed=1)

    result = _run_isolated_strategy_replay(
        Adapter(),
        b"STAMPED",
        [],
        object(),
        htf_bars=htf_bars,
    )
    assert result.ok is True
    assert seen["htf_bars"] == htf_bars


def test_strategy_replay_wires_isolated_htf_bars() -> None:
    source = inspect.getsource(strategies.strategy_replay)
    assert "_run_isolated_strategy_replay" in source
    assert "_confirmed_htf_bars_for_replay" in source
    assert "htf_bars=" in source


def test_confirmed_htf_bars_stamp_provider_bars_with_time_close() -> None:
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]
    assert _confirmed_htf_bars_from_provider_bars(
        bars, symbol="btcusdt", timeframe="1m"
    ) == [
        {
            "symbol": "btcusdt",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]


def test_confirmed_htf_bars_do_not_invent_time_close() -> None:
    assert (
        _confirmed_htf_bars_from_provider_bars(
            [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
            symbol="BTCUSDT",
            timeframe="1m",
        )
        is None
    )


def test_gateway_replay_stamps_confirmed_provider_htf_bars(
    monkeypatch, job_store
) -> None:
    seen: dict[str, object] = {}
    broadcasts: list[object] = []

    async def broadcast(update):
        broadcasts.append(update)

    import openpine.gateway.ws_manager as ws_mod

    monkeypatch.setattr(ws_mod.ws_manager, "broadcast", broadcast)

    md_contracts = types.ModuleType("marketdata_provider.contracts")
    md_contracts.InstrumentKey = lambda **kw: SimpleNamespace(**kw)
    md_contracts.BarQuery = lambda **kw: SimpleNamespace(**kw)
    md_contracts.parse_timeframe = lambda tf: SimpleNamespace(value=tf, duration_ms=60_000)
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts", md_contracts)

    class DataOrchestrator:
        def get_bars(self, query):
            return [
                SimpleNamespace(
                    time=0,
                    time_close=59_999,
                    open=1,
                    high=2,
                    low=0.5,
                    close=1.5,
                    volume=3,
                )
            ]

    class BacktestRunConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class BacktestEngineAdapter:
        def run_isolated(self, *a, **k):
            seen["htf_bars"] = k.get("htf_bars")
            return SimpleNamespace(bars_processed=1)

    rt = types.ModuleType("openpine.runtime.engine")
    rt.BacktestRunConfig = BacktestRunConfig
    rt.BacktestEngineAdapter = BacktestEngineAdapter
    monkeypatch.setitem(sys.modules, "openpine.runtime.engine", rt)

    iso = types.ModuleType("openpine.runtime.isolated_run")
    iso.capture_generated_source = lambda *a, **k: b"src"
    monkeypatch.setitem(sys.modules, "openpine.runtime.isolated_run", iso)

    class Registry:
        def get_strategy(self, strategy_id):
            return SimpleNamespace(
                strategy_id=strategy_id,
                pine_id="p",
                artifact_id="a",
                symbol="btcusdt",
                timeframe="1m",
                exchange="binance",
                market_type="spot",
                semantic_profile="strict_5x",
                status="paused",
            )

        def update_status(self, strategy_id, status):
            return None

    async def _call():
        response = await strategies.strategy_replay(
            "s1",
            state=SimpleNamespace(orchestrator=DataOrchestrator(), job_store=job_store),
            registry=Registry(),
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return response

    response = asyncio.run(_call())
    assert response["status"] == "started"
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]
    assert broadcasts[-1].status == "completed"

def test_replay_helper_fetches_explicit_htf_timeframe() -> None:
    loaded = []
    chart = [
        SimpleNamespace(time=0, time_close=59_999, open=1, high=2, low=0.5, close=1.5, volume=3)
    ]
    fetched = [
        SimpleNamespace(time=0, time_close=86_399_999, open=40, high=43, low=39, close=42, volume=1)
    ]

    def load_bars(query):
        loaded.append(str(getattr(query.timeframe, "canonical", query.timeframe)))
        return fetched

    stamped = _confirmed_htf_bars_for_replay(
        chart,
        symbol="BTCUSDT",
        chart_timeframe="1m",
        requested_timeframe="1D",
        load_bars=load_bars,
        instrument=object(),
        start_ms=0,
        end_ms=60_000,
    )
    assert loaded == ["1D"]
    assert stamped == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_replay_helper_same_timeframe_does_not_refetch() -> None:
    loaded = []
    chart = [
        SimpleNamespace(time=0, time_close=59_999, open=1, high=2, low=0.5, close=1.5, volume=3)
    ]
    stamped = _confirmed_htf_bars_for_replay(
        chart,
        symbol="BTCUSDT",
        chart_timeframe="1m",
        requested_timeframe="1m",
        load_bars=lambda query: loaded.append(query) or chart,
        instrument=object(),
        start_ms=0,
        end_ms=60_000,
    )
    assert loaded == []
    assert stamped[0]["timeframe"] == "1m"


def test_gateway_replay_fetches_explicit_htf_timeframe(
    monkeypatch, job_store
) -> None:
    seen = {}
    loaded = []

    async def broadcast(update):
        return None

    import openpine.gateway.ws_manager as ws_mod
    monkeypatch.setattr(ws_mod.ws_manager, "broadcast", broadcast)

    md_contracts = types.ModuleType("marketdata_provider.contracts")
    md_contracts.InstrumentKey = lambda **kw: SimpleNamespace(**kw)
    md_contracts.BarQuery = lambda **kw: SimpleNamespace(**kw)
    md_contracts.parse_timeframe = lambda tf: SimpleNamespace(value=tf, duration_ms=60_000)
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts", md_contracts)

    class DataOrchestrator:
        def get_bars(self, query):
            tf = getattr(query.timeframe, "value", query.timeframe)
            loaded.append(str(tf))
            if str(tf) == "1D":
                return [
                    SimpleNamespace(
                        time=0, time_close=86_399_999, open=40, high=43, low=39, close=42, volume=1
                    )
                ]
            return [
                SimpleNamespace(time=0, time_close=59_999, open=1, high=2, low=0.5, close=1.5, volume=3)
            ]

    class BacktestRunConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class BacktestEngineAdapter:
        def run_isolated(self, *a, **k):
            seen["htf_bars"] = k.get("htf_bars")
            return SimpleNamespace(bars_processed=1)

    rt = types.ModuleType("openpine.runtime.engine")
    rt.BacktestRunConfig = BacktestRunConfig
    rt.BacktestEngineAdapter = BacktestEngineAdapter
    monkeypatch.setitem(sys.modules, "openpine.runtime.engine", rt)

    iso = types.ModuleType("openpine.runtime.isolated_run")
    iso.capture_generated_source = lambda *a, **k: b"src"
    monkeypatch.setitem(sys.modules, "openpine.runtime.isolated_run", iso)

    class Registry:
        def get_strategy(self, strategy_id):
            return SimpleNamespace(
                strategy_id=strategy_id,
                pine_id="p",
                artifact_id="a",
                symbol="btcusdt",
                timeframe="1m",
                exchange="binance",
                market_type="spot",
                semantic_profile="strict_5x",
                status="paused",
            )

        def update_status(self, strategy_id, status):
            return None

    async def _call():
        response = await strategies.strategy_replay(
            "s1",
            body=ReplayRequest(htf_timeframe="1D"),
            state=SimpleNamespace(orchestrator=DataOrchestrator(), job_store=job_store),
            registry=Registry(),
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return response

    response = asyncio.run(_call())
    assert response["status"] == "started"
    assert "1D" in loaded
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]
