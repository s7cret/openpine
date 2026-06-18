from __future__ import annotations

import asyncio
from types import SimpleNamespace

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.gateway.routes import backtest as bt


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _Storage:
    def execute(self, sql, params=()):
        if str(sql).startswith("PRAGMA"):
            return _Cursor([(0, "run_id"), (1, "data_fingerprint")])
        return _Cursor()

    def commit(self):
        pass


class _Store:
    def __init__(self):
        self.saved = []
        self.failed = []

    def save_result(self, **kwargs):
        self.saved.append(kwargs)

    def mark_failed(self, run_id, message):
        self.failed.append((run_id, message))

    def mark_cancelled(self, run_id, message):
        pass


class _WS:
    def update_progress(self, *args, **kwargs):
        pass

    async def broadcast_progress(self, *args, **kwargs):
        pass


def _series():
    inst = InstrumentKey(exchange="binance", market="spot", symbol="SOLUSDT")
    tf = parse_timeframe("1d")
    bars = (
        Bar(inst, tf, 0, 86_400_000, 10.0, 11.0, 9.0, 10.5, 1.0, True),
        Bar(inst, tf, 86_400_000, 172_800_000, 10.5, 12.0, 10.0, 11.0, 1.0, True),
    )
    query = BarQuery(inst, tf, 0, 172_800_000, gap_policy="allow_with_metadata")
    coverage = CoverageReport(0, 172_800_000, 0, 172_800_000, source_mix=("unit",))
    return BarSeries(query, bars, coverage)


def test_gateway_backtest_config_uses_exchange_market_metadata(monkeypatch):
    captured = {}
    monkeypatch.setattr(bt, "ws_manager", _WS())

    import openpine.runtime.engine as runtime_engine
    import openpine.data.provider_adapter as provider_adapter

    monkeypatch.setattr(
        runtime_engine,
        "load_strategy_class_from_artifact",
        lambda *args, **kwargs: type("GeneratedStrategy", (), {}),
    )
    monkeypatch.setattr(provider_adapter, "create_local_runtime_data_provider_adapter", lambda **kwargs: None)

    def run_in_process(adapter, strategy_class, bars, config, params, runtime_data_provider, progress_callback=None):
        captured["config"] = config
        return SimpleNamespace(raw_result=SimpleNamespace(trades=[], equity_curve=None))

    monkeypatch.setattr(bt, "_run_backtest_in_process", run_in_process)

    strategy = SimpleNamespace(
        strategy_id="s1",
        pine_id="p1",
        artifact_id="a1",
        params_hash="ph1",
        exchange="BINANCE",
        market_type="SPOT",
        symbol="SOLUSDT",
        timeframe="1d",
        params_json="{}",
    )
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        orchestrator=SimpleNamespace(load_bars=lambda query, progress_callback=None: _series()),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: {
                "compile_meta": {
                    "translation_metadata": {
                        "declaration": {
                            "arguments": {
                                "default_qty_type": "percent_of_equity",
                                "default_qty_value": 80,
                            }
                        }
                    }
                }
            }
        ),
        backtest_store=_Store(),
        backtest_cancel_requests=set(),
        storage=_Storage(),
        config=SimpleNamespace(data_cache_root=None, data_dir=None),
    )

    asyncio.run(bt._run_backtest_background(state, "s1", "run1", 0, 172_800_000, None, 0, False))

    config = captured["config"]
    assert config.mintick == 0.01
    assert config.qty_step == 0.001
    assert config.qty_rounding_mode == "truncate"
    assert state.backtest_store.saved
