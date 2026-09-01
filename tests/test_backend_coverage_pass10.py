from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway.routes import backtest as backtest_routes
from openpine.runtime.engine import BacktestArtifactError
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest, canonical_series


def _bar(t: int = 0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, 1.0, 1.0, 1.0, 1.0, 1.0, True)


def _series(bars: tuple[Bar, ...] | None = None):
    bars = bars if bars is not None else (_bar(0), _bar(60_000))
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    query = BarQuery(inst, tf, 0, 120_000, gap_policy="allow_with_metadata")
    coverage = CoverageReport(0, 120_000, bars[0].time if bars else None, bars[-1].time_close if bars else None, source_mix=("test",))
    return canonical_series(BarSeries(query, bars, coverage))


class FakeRegistry:
    def __init__(self, strategy=None, fail: bool = False):
        self.strategy = strategy or SimpleNamespace(
            strategy_id="s1",
            pine_id="p1",
            artifact_id="a1",
            params_hash="h",
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            params_json='{"len": 7}',
            semantic_profile="strict_5x",
        )
        self.fail = fail

    def get_strategy(self, strategy_id: str):
        if self.fail:
            raise KeyError(strategy_id)
        return self.strategy


class FakeBacktestStore:
    def __init__(self):
        self.failed: list[str] = []
        self.cancelled: list[str] = []
        self.saved: list[dict] = []

    def mark_failed(self, run_id: str, message: str):
        self.failed.append(message)

    def mark_cancelled(self, run_id: str, message: str):
        self.cancelled.append(message)

    def save_result(self, **kwargs):
        self.saved.append(kwargs)


class FakeStorage:
    def execute(self, *args, **kwargs):
        raise RuntimeError("no sqlite in unit fake")

    def commit(self):
        pass


class FakeArtifactStore:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def get_artifact(self, artifact_id: str, pine_id: str):
        if self.fail:
            raise RuntimeError("artifact meta unavailable")
        return make_sealed_artifact(
            {
                "translation_metadata": {
                    "declaration": {
                        "arguments": {
                            "commission_type": "cash_per_order",
                            "initial_capital": 1234.0,
                            "default_qty_type": "percent_of_equity",
                            "default_qty_value": 5.0,
                            "process_orders_on_close": True,
                        }
                    }
                }
            }
        )


class FakeOrchestrator:
    def __init__(self, series: BarSeries | None = None, fail: bool = False):
        self.series = series if series is not None else _series()
        self.fail = fail

    def load_bars(self, query, progress_callback=None):
        if self.fail:
            raise RuntimeError("data boom")
        if progress_callback:
            progress_callback(1, 1, len(self.series.bars), 1, 0, "cache")
        return self.series


def _state(**kwargs):
    return SimpleNamespace(
        strategy_registry=kwargs.get("registry") or FakeRegistry(),
        backtest_store=kwargs.get("store") or FakeBacktestStore(),
        backtest_cancel_requests=kwargs.get("cancel") or set(),
        artifact_store=kwargs.get("artifact_store") or FakeArtifactStore(),
        orchestrator=kwargs.get("orchestrator") or FakeOrchestrator(),
        storage=FakeStorage(),
        config=SimpleNamespace(data_dir=Path(".openpine"), data_cache_root=None),
        admission_identity=make_deployment_identity(),
        admitted_manifest=admitted_manifest(),
    )
