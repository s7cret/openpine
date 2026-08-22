from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway.routes import backtest as backtest_routes
from openpine.runtime.engine import BacktestArtifactError
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact


def _bar(t: int = 0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, 1.0, 1.0, 1.0, 1.0, 1.0, True)


def _series(bars: tuple[Bar, ...] | None = None) -> BarSeries:
    bars = bars if bars is not None else (_bar(0), _bar(60_000))
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    query = BarQuery(inst, tf, 0, 120_000, gap_policy="allow_with_metadata")
    coverage = CoverageReport(0, 120_000, bars[0].time if bars else None, bars[-1].time_close if bars else None, source_mix=("test",))
    return BarSeries(query, bars, coverage)


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
    )


def _patch_runtime(monkeypatch, *, result=None, artifact_error: Exception | None = None, run_error: Exception | None = None, provider_error: Exception | None = None):
    import openpine.runtime.engine as runtime_engine
    import openpine.data.direct_data_provider as direct_data_provider
    import openpine.runtime.isolated_run as isolated_run

    def load_strategy(*args, **kwargs):
        if artifact_error:
            raise artifact_error
        return type("Strategy", (), {})

    def capture_source(*args, **kwargs):
        if artifact_error:
            raise artifact_error
        return b"generated-source"

    class Adapter:
        pass

    class Provider:
        def __init__(self, *args, **kwargs):
            if provider_error:
                raise provider_error

    def run_in_process(*args, **kwargs):
        if run_error:
            raise run_error
        return result or SimpleNamespace(raw_result=SimpleNamespace(trades=[], equity_curve=None), bars_processed=2)

    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", load_strategy)
    monkeypatch.setattr(isolated_run, "capture_generated_source", capture_source)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", Adapter)
    monkeypatch.setattr(direct_data_provider, "DirectBinanceDataProvider", Provider)
    monkeypatch.setattr(backtest_routes, "_run_backtest_in_process", run_in_process)
    monkeypatch.setattr(backtest_routes, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: SimpleNamespace(estimated_bars=2, estimated_pages=1, effective_from=from_ms, effective_to=to_ms, adjusted=False))


def test_run_backtest_background_success_and_runtime_error(monkeypatch):
    _patch_runtime(monkeypatch)
    state = _state()
    asyncio.run(backtest_routes._run_backtest_background(state, "s1", "r1", 0, 120_000, None, 0, True))
    assert state.backtest_store.saved and state.backtest_store.saved[0]["run_id"] == "r1"

    _patch_runtime(monkeypatch, run_error=RuntimeError("compute failed"), provider_error=RuntimeError("provider optional"))
    state2 = _state()
    asyncio.run(backtest_routes._run_backtest_background(state2, "s1", "r2", 0, 120_000, {"x": 1}, 0, False))
    assert any("compute failed" in msg for msg in state2.backtest_store.failed)


def test_background_retains_lease_until_unproven_worker_unregisters(monkeypatch):
    run_id = "retained-background-lease"
    limiter = backtest_routes._BacktestAdmissionLimiter(1)
    lease = limiter.try_acquire()
    assert lease is not None
    worker = backtest_routes._BacktestWorker(
        process=SimpleNamespace(
            pid=None,
            is_alive=lambda: False,
            join=lambda timeout=None: None,
        ),
        out=SimpleNamespace(
            close=lambda: None,
            cancel_join_thread=lambda: None,
        ),
        process_group=None,
        start_time=None,
    )
    replacement = None
    _patch_runtime(monkeypatch)

    def fail_with_retained_worker(*args, **kwargs):
        backtest_routes._retain_backtest_worker_for_retry(run_id, worker)
        raise RuntimeError("cleanup unproven")

    monkeypatch.setattr(
        backtest_routes, "_run_backtest_in_process", fail_with_retained_worker
    )
    state = _state()
    try:
        asyncio.run(
            backtest_routes._run_backtest_background(
                state,
                "s1",
                run_id,
                0,
                120_000,
                None,
                0,
                False,
                admission_lease=lease,
            )
        )

        assert state.backtest_store.failed == ["cleanup unproven"]
        assert backtest_routes._backtest_terminal_outcome(run_id) == "failed"
        assert limiter.try_acquire() is None

        backtest_routes._unregister_backtest_worker(run_id, worker)
        replacement = limiter.try_acquire()
        assert replacement is not None
    finally:
        backtest_routes._unregister_backtest_worker(run_id, worker)
        if replacement is not None:
            replacement.release()
        lease.release()
        with backtest_routes._ACTIVE_BACKTEST_WORKERS_LOCK:
            backtest_routes._TERMINAL_BACKTEST_RUNS.discard(run_id)
            backtest_routes._TERMINAL_BACKTEST_OUTCOMES.pop(run_id, None)
            backtest_routes._RETAINED_BACKTEST_LEASES.pop(run_id, None)


def test_run_backtest_background_failure_and_cancel_paths(monkeypatch):
    _patch_runtime(monkeypatch, artifact_error=BacktestArtifactError("bad artifact"))
    state = _state(
        artifact_store=SimpleNamespace(
            get_artifact=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                BacktestArtifactError("bad artifact")
            )
        )
    )
    asyncio.run(backtest_routes._run_backtest_background(state, "s1", "r1", 0, 120_000, None, 0, False))
    assert state.backtest_store.failed == ["bad artifact"]

    _patch_runtime(monkeypatch)
    missing = _state(registry=FakeRegistry(fail=True))
    asyncio.run(backtest_routes._run_backtest_background(missing, "s1", "r2", 0, 120_000, None, 0, False))
    assert missing.backtest_store.failed == ["Strategy not found"]

    data_fail = _state(orchestrator=FakeOrchestrator(fail=True))
    asyncio.run(backtest_routes._run_backtest_background(data_fail, "s1", "r3", 0, 120_000, None, 0, False))
    assert any("Data load failed" in msg for msg in data_fail.backtest_store.failed)

    no_bars = _state(orchestrator=FakeOrchestrator(_series(())))
    asyncio.run(backtest_routes._run_backtest_background(no_bars, "s1", "r4", 0, 120_000, None, 0, False))
    assert no_bars.backtest_store.failed == ["No bars found in range"]

    cancelled = _state(cancel={"r5"})
    asyncio.run(backtest_routes._run_backtest_background(cancelled, "s1", "r5", 0, 120_000, None, 0, False))
    assert cancelled.backtest_store.cancelled
