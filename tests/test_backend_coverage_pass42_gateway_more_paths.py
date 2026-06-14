from __future__ import annotations

import asyncio
import json
import queue
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.gateway import live_runner as lr
from openpine.gateway.routes import accounts_data as ad
from openpine.gateway.routes import backtest as bt
from openpine.gateway.schemas import (
    BacktestEstimateResponse,
    BacktestRunRequest,
    DataBackfillRequest,
)


@pytest.fixture(autouse=True)
def _no_default_live_state_store(monkeypatch):
    monkeypatch.setattr(
        lr.LiveStrategyRunner, "_default_state_store", staticmethod(lambda: None)
    )


def _strategy(**overrides):
    values = dict(
        strategy_id="s1",
        pine_id="p1",
        artifact_id="a1",
        params_hash="ph1",
        exchange="BINANCE",
        market_type="SPOT",
        symbol="btcusdt",
        timeframe="1m",
        name="Strategy",
        params_json='{"length": 7}',
        enabled=True,
        status="running",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close + 1, close - 1, close, 10.0, True)


def _series(times=(0, 60_000)) -> BarSeries:
    bars = tuple(_bar(t, float(i + 1)) for i, t in enumerate(times))
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    end_ms = (max(times) + 60_000) if times else 60_000
    query = BarQuery(inst, tf, min(times) if times else 0, end_ms, gap_policy="allow_with_metadata")
    coverage = CoverageReport(
        query.start_ms,
        query.end_ms,
        bars[0].time if bars else None,
        bars[-1].time_close if bars else None,
        source_mix=("unit",),
    )
    return BarSeries(query, bars, coverage)


class _Cursor:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one if self._one is not None else (self._rows[0] if self._rows else None)

    def fetchall(self):
        return list(self._rows)


class _FakeWS:
    def __init__(self):
        self.events: list[dict[str, object]] = []
        self.progress: dict[str, object] | None = None

    def update_progress(self, operation_id, domain, status, pct, message, detail=None):
        event = {
            "operation_id": operation_id,
            "domain": domain,
            "status": status,
            "pct": pct,
            "message": message,
            "detail": detail,
        }
        self.events.append(event)
        self.progress = event

    async def broadcast_progress(self, operation_id):
        self.events.append({"operation_id": operation_id, "status": "broadcast"})

    def get_progress(self, operation_id):
        return self.progress


class _FingerprintStorage:
    def __init__(self, *, has_column: bool = False):
        self.has_column = has_column
        self.sql: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.sql.append((sql, tuple(params)))
        if str(sql).startswith("PRAGMA"):
            rows = [(0, "run_id")] + ([(1, "data_fingerprint")] if self.has_column else [])
            return _Cursor(rows=rows)
        return _Cursor(one=(0,), rows=[])

    def commit(self):
        self.commits += 1


class _BacktestStore:
    def __init__(self, *, fail_mark_failed: bool = False):
        self.fail_mark_failed = fail_mark_failed
        self.failed: list[tuple[str, str]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.saved: list[dict[str, object]] = []
        self.created: list[object] = []
        self.runs: list[object] = []
        self.metrics_exc = False
        self.artifacts: list[object] = []
        self.trades: list[object] = []

    def mark_failed(self, run_id, message):
        if self.fail_mark_failed:
            raise RuntimeError("mark failed exploded")
        self.failed.append((run_id, message))

    def mark_cancelled(self, run_id, message):
        self.cancelled.append((run_id, message))

    def save_result(self, **kwargs):
        self.saved.append(kwargs)

    def create_run(self, request):
        self.created.append(request)
        return "run-created"

    def list_runs(self, strategy_id, limit=50):
        return [r for r in self.runs if r.strategy_id == strategy_id][:limit]

    def list_all_runs(self, limit=50):
        return self.runs[:limit]

    def get_run(self, run_id):
        return next((r for r in self.runs if r.run_id == run_id), None)

    def get_metrics(self, run_id):
        if self.metrics_exc:
            raise RuntimeError("metrics down")
        return {"metrics": {"total_trades": 2}}

    def list_artifacts(self, run_id):
        return list(self.artifacts)

    def list_trades(self, run_id):
        return list(self.trades)

    def delete_run(self, run_id):
        return False


def _run_row(run_id="r1", *, strategy_id="s1", status="done", started_at=1):
    return SimpleNamespace(
        run_id=run_id,
        strategy_id=strategy_id,
        status=status,
        started_at=started_at,
        finished_at=started_at + 10,
        symbol="BTCUSDT",
        timeframe="1m",
        from_time=0,
        to_time=60_000,
        bars_processed=3,
    )


def _patch_backtest_runtime(monkeypatch, *, run_error=None, provider_error=None):
    import openpine.data.direct_data_provider as direct_data_provider
    import openpine.runtime.engine as runtime_engine

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeAdapter:
        pass

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            if provider_error is not None:
                raise provider_error

    def run_in_process(*args, **kwargs):
        progress_callback = args[-1] if args else kwargs.get("progress_callback")
        if progress_callback is not None:
            progress_callback(2, 4)
        if run_error is not None:
            raise run_error
        return SimpleNamespace(
            raw_result=SimpleNamespace(trades=[], equity_curve=None), bars_processed=4
        )

    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", lambda *a, **k: type("Generated", (), {}))
    monkeypatch.setattr(runtime_engine, "BacktestRunConfig", FakeConfig)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", FakeAdapter)
    monkeypatch.setattr(direct_data_provider, "DirectBinanceDataProvider", FakeProvider)
    monkeypatch.setattr(bt, "_run_backtest_in_process", run_in_process)
    monkeypatch.setattr(
        bt,
        "_estimate_backtest_market_data",
        lambda strategy, from_ms, to_ms: SimpleNamespace(
            estimated_bars=4,
            estimated_pages=1,
            effective_from=from_ms,
            effective_to=to_ms,
            earliest_available=from_ms,
            adjusted=False,
            model_dump=lambda: {},
        ),
    )


class _CancelAfter:
    def __init__(self, trigger_call: int):
        self.trigger_call = trigger_call
        self.calls = 0
        self.discarded: list[str] = []

    def __contains__(self, item):
        self.calls += 1
        return self.calls == self.trigger_call

    def discard(self, item):
        self.discarded.append(item)


def _backtest_state(*, store=None, cancel=None, registry=None, orchestrator=None, storage=None, artifact_store=None):
    return SimpleNamespace(
        strategy_registry=registry
        or SimpleNamespace(get_strategy=lambda strategy_id: _strategy(strategy_id=strategy_id)),
        backtest_store=store or _BacktestStore(),
        backtest_cancel_requests=cancel if cancel is not None else set(),
        artifact_store=artifact_store
        or SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: {
                "compile_meta": {
                    "translation_metadata": {
                        "declaration": {
                            "arguments": {
                                "commission_type": "cash_per_order",
                                "initial_capital": 1234.0,
                                "process_orders_on_close": True,
                            }
                        }
                    }
                }
            }
        ),
        orchestrator=orchestrator
        or SimpleNamespace(
            load_bars=lambda query, progress_callback=None: (
                progress_callback(1, 1, 4, 1, query.start_ms, "cache")
                if progress_callback
                else None,
                _series((query.start_ms, query.start_ms + 60_000, query.start_ms + 120_000)),
            )[1]
        ),
        storage=storage or _FingerprintStorage(),
    )


def test_backtest_estimate_worker_queue_and_process_edges(monkeypatch):
    estimate = bt._estimate_backtest_market_data(_strategy(), 0, 180_000)
    assert estimate.effective_from == 0
    assert estimate.exchange == "binance"
    assert estimate.market_type == "spot"
    assert estimate.adjusted is False
    assert estimate.estimated_bars == 4
    assert estimate.estimated_pages == 1

    class OutWithBadProgress:
        def __init__(self):
            self.items = []

        def put_nowait(self, item):
            raise RuntimeError("progress queue full")

        def put(self, item):
            self.items.append(item)

    class Adapter:
        def run(self, *args, progress_callback=None, **kwargs):
            progress_callback(1, 2)
            return "ok-result"

    out = OutWithBadProgress()
    bt._backtest_process_entry(out, Adapter(), object, [], object(), {}, None)
    assert out.items == [("ok", "ok-result")]

    class FakeQueue:
        def __init__(self, get_items=(), nowait_items=()):
            self.get_items = list(get_items)
            self.nowait_items = list(nowait_items)

        def get(self, timeout=0):
            if self.get_items:
                item = self.get_items.pop(0)
                if item is queue.Empty:
                    raise queue.Empty
                return item
            raise queue.Empty

        def get_nowait(self):
            if self.nowait_items:
                item = self.nowait_items.pop(0)
                if item is queue.Empty:
                    raise queue.Empty
                return item
            raise queue.Empty

        def close(self):
            pass

        def cancel_join_thread(self):
            pass

    class FakeProc:
        def __init__(self, *, exitcode=0, alive=(False,)):
            self.exitcode = exitcode
            self.alive = list(alive)

        def start(self):
            pass

        def is_alive(self):
            return self.alive.pop(0) if self.alive else False

        def join(self):
            pass

    class Ctx:
        def __init__(self, q, proc):
            self.q = q
            self.proc = proc

        def Queue(self):
            return self.q

        def Process(self, **kwargs):
            return self.proc

    progress = []
    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(
            FakeQueue([queue.Empty, ("ok", "done-after-empty")]),
            FakeProc(alive=(True, True, False)),
        ),
    )
    assert bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None, lambda d, t: progress.append((d, t))) == "done-after-empty"

    drained = []
    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(
            FakeQueue([queue.Empty], [("progress", 2, 4), ("ok", "late-ok")]),
            FakeProc(alive=(False, False)),
        ),
    )
    assert bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None, lambda d, t: drained.append((d, t))) == "late-ok"
    assert drained == [(2, 4)]

    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(FakeQueue([]), FakeProc(exitcode=9, alive=(False, False))),
    )
    with pytest.raises(RuntimeError, match="code 9"):
        bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None)


def test_backtest_progress_source_label_uses_strategy_exchange_market():
    strategy = SimpleNamespace(
        strategy_id="s1",
        symbol="BTCUSDT",
        timeframe="1h",
        exchange="bybit",
        market_type="futures",
    )
    query = bt._market_data_query_for_strategy(strategy, 0, 60_000)

    assert bt._backtest_progress_source_label("fetch", query) == "bybit futures"
    assert bt._backtest_progress_source_label("cache-hit", query) == "cache"


def test_strategy_replay_uses_strategy_market_contract_not_binance_stub():
    from openpine.gateway.routes import strategies
    import inspect

    source = inspect.getsource(strategies.strategy_replay)

    assert 'exchange="binance"' not in source
    assert 'market_type="futures"' not in source
    assert "price_type" not in source
    assert "DataOrchestrator()" not in source


def test_backtest_background_cancel_progress_and_failure_mark_failed(monkeypatch):
    ws = _FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    _patch_backtest_runtime(monkeypatch, provider_error=RuntimeError("optional provider down"))

    for trigger, phase in [(2, "artifact load"), (3, "market data load"), (4, "backtest setup")]:
        store = _BacktestStore()
        state = _backtest_state(store=store, cancel=_CancelAfter(trigger))
        asyncio.run(bt._run_backtest_background(state, "s1", "run-cancel", 0, 240_000, None, 0, False))
        assert store.cancelled and phase in store.cancelled[-1][1]

    store = _BacktestStore()
    state = _backtest_state(store=store, cancel=_CancelAfter(5))
    asyncio.run(bt._run_backtest_background(state, "s1", "run-compute-cancel", 0, 240_000, {"fast": 5}, 0, True))
    assert store.cancelled and "compute" in store.cancelled[-1][1]
    assert any(event.get("message") == "Bars: 2/4" for event in ws.events)
    assert not store.saved

    bad_store = _BacktestStore(fail_mark_failed=True)
    bad_registry = SimpleNamespace(
        get_strategy=lambda strategy_id: (_ for _ in ()).throw(RuntimeError("registry exploded"))
    )
    asyncio.run(
        bt._run_backtest_background(
            _backtest_state(store=bad_store, registry=bad_registry),
            "s1",
            "run-outer-fail",
            0,
            60_000,
            None,
            0,
            False,
        )
    )
    assert any(event.get("status") == "failed" for event in ws.events)


def test_backtest_routes_listing_estimate_run_and_artifact_error_paths(monkeypatch, tmp_path):
    store = _BacktestStore()
    store.runs = [_run_row("old", started_at=1), _run_row("new", started_at=2)]
    store.metrics_exc = True
    state = _backtest_state(store=store)
    state.strategy_registry = SimpleNamespace(get_strategy=lambda strategy_id: (_ for _ in ()).throw(KeyError(strategy_id)))

    with pytest.raises(HTTPException) as bad_range:
        asyncio.run(bt.estimate_backtest("s1", "2", "1", state))
    assert bad_range.value.status_code == 400

    with pytest.raises(HTTPException) as missing_strategy:
        asyncio.run(bt.estimate_backtest("missing", "1", "2", state))
    assert missing_strategy.value.status_code == 404

    estimate = BacktestEstimateResponse(
        strategy_id="s1",
        symbol="BTCUSDT",
        timeframe="1m",
        requested_from=1,
        requested_to=2,
        effective_from=1,
        effective_to=2,
        earliest_available=1,
        adjusted=True,
        estimated_bars=1,
        estimated_pages=1,
    )
    monkeypatch.setattr(bt, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: estimate)
    state.strategy_registry = SimpleNamespace(get_strategy=lambda strategy_id: _strategy(strategy_id=strategy_id))
    assert asyncio.run(bt.estimate_backtest("s1", "1", "2", state)).adjusted is True

    ws = _FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    response = asyncio.run(
        bt.run_backtest(
            BacktestRunRequest(strategy_id="s1", from_time="1", to_time="2", capture_plots=True),
            BackgroundTasks(),
            state,
        )
    )
    assert response.run_id == "run-created"
    assert "Range adjusted" in ws.events[-1]["message"]

    state.strategy_registry = SimpleNamespace(get_strategy=lambda strategy_id: (_ for _ in ()).throw(RuntimeError("name down")))
    listed = asyncio.run(bt.list_runs(strategy_id="s1", state=state))
    assert listed and listed[0].strategy_name is None and listed[0].metrics is None

    detail = asyncio.run(bt.get_run("new", state))
    assert detail.strategy_name is None and detail.metrics is None and detail.version == 2

    import pandas as pd

    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda path: SimpleNamespace(to_csv=lambda index=False: "a,b\n1,2\n"),
    )
    assert bt._read_parquet_as_csv("ignored.parquet") == "a,b\n1,2\n"

    monkeypatch.setattr(bt, "_read_parquet_as_csv", lambda path: (_ for _ in ()).throw(RuntimeError("parquet boom")))
    store.artifacts = [SimpleNamespace(artifact_type="plot_outputs", path="plot.parquet")]
    with pytest.raises(HTTPException) as plot_error:
        asyncio.run(bt.get_run_plots("new", state))
    assert plot_error.value.status_code == 500

    store.artifacts = [SimpleNamespace(artifact_type="bar_outputs", path="bars.parquet")]
    with pytest.raises(HTTPException) as bar_error:
        asyncio.run(bt.get_run_bar_outputs("new", state))
    assert bar_error.value.status_code == 500

    store.artifacts = []
    with pytest.raises(HTTPException) as missing_report:
        asyncio.run(bt.get_run_report("new", state))
    assert missing_report.value.status_code == 404

    store.artifacts = [SimpleNamespace(artifact_type="report_md", path=str(tmp_path / "missing.md"))]
    with pytest.raises(HTTPException) as report_error:
        asyncio.run(bt.get_run_report("new", state))
    assert report_error.value.status_code == 500


def test_live_runner_loop_process_strategy_and_small_helpers(monkeypatch):
    runner = lr.LiveStrategyRunner(state_store=None)

    async def cancelled_check():
        raise asyncio.CancelledError

    runner._running = True
    monkeypatch.setattr(runner, "_check_all_strategies", cancelled_check)
    asyncio.run(runner._run_loop())

    class Registry:
        def list_strategies(self):
            return [_strategy(enabled=False), _strategy(strategy_id="s2", status="paused")]

    asyncio.run(lr.LiveStrategyRunner(registry=Registry(), state_store=None)._check_all_strategies())

    import marketdata_provider.contracts as contracts

    with monkeypatch.context() as m:
        m.setattr(contracts, "parse_timeframe", lambda value: SimpleNamespace(duration_ms=None))
        asyncio.run(lr.LiveStrategyRunner(state_store=None)._process_strategy(_strategy(), 123_456))

    runner = lr.LiveStrategyRunner(state_store=None)
    runner._strategy_states["s1"] = lr.StrategyBarState("s1", 60_000)
    monkeypatch.setattr(
        runner,
        "_run_mini_backtest",
        lambda strategy, bar_time: (_ for _ in ()).throw(RuntimeError("mini failed")),
    )
    asyncio.run(runner._process_strategy(_strategy(), 240_001))
    assert runner._strategy_states["s1"].last_bar_time_ms == 60_000

    asyncio.run(runner._process_orders(_strategy(), []))

    class BadMatch:
        def group(self, index):
            return "not-a-float"

    with monkeypatch.context() as m:
        m.setattr(lr.re, "search", lambda *args, **kwargs: BadMatch())
        assert lr.LiveStrategyRunner._extract_percent_input("tpPct=input.float(1)", "tpPct") is None

    class BadStorage:
        def execute(self, *args, **kwargs):
            raise RuntimeError("source query down")

    assert lr.LiveStrategyRunner(storage=BadStorage())._strategy_risk_percents(_strategy()) == (None, None)

    class EmptyStorage:
        def execute(self, *args, **kwargs):
            return _Cursor(one=None)

    assert lr.LiveStrategyRunner(storage=EmptyStorage())._strategy_risk_percents(_strategy()) == (None, None)

    runner = lr.LiveStrategyRunner(state_store=None)
    assert runner._load_resume_snapshot(_strategy(), instrument_key={}, timeframe={}, at_or_before_bar_time=1) is None
    runner._save_resume_snapshot(
        _strategy(),
        result=SimpleNamespace(resume_state={"runtime_state": {}}),
        instrument_key={},
        timeframe={},
        bar_time=1,
        data_fingerprint="fp",
    )
    runner._mark_resume_snapshot_invalid(_strategy(), 1)

    class SaveStore:
        def save_runtime_snapshot(self, **kwargs):
            raise AssertionError("should not save without resume_state")

    lr.LiveStrategyRunner(state_store=SaveStore())._save_resume_snapshot(
        _strategy(),
        result=SimpleNamespace(resume_state=None),
        instrument_key={},
        timeframe={},
        bar_time=1,
        data_fingerprint="fp",
    )


def _patch_live_runtime(monkeypatch, adapter_cls, *, provider=None):
    import openpine.data.provider_adapter as provider_adapter
    import openpine.runtime.engine as runtime_engine

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", lambda *a, **k: type("Generated", (), {}))
    monkeypatch.setattr(runtime_engine, "BacktestRunConfig", FakeConfig)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", adapter_cls)
    monkeypatch.setattr(
        provider_adapter,
        "create_local_runtime_data_provider_adapter",
        provider or (lambda *a, **k: object()),
    )


class _StateStore:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.saved: list[dict[str, object]] = []
        self.invalidated: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def latest_snapshot_metadata(self, *args, **kwargs):
        return self.snapshot

    def load_latest_compatible(self, *args, **kwargs):
        return self.snapshot

    def save_runtime_snapshot(self, **kwargs):
        self.saved.append(kwargs)

    def mark_invalid(self, *args, **kwargs):
        self.invalidated.append((args, kwargs))


def test_live_runner_mini_backtest_resume_rebase_empty_and_optional_failures(monkeypatch):
    class EmptyAdapter:
        def run(self, *args, **kwargs):
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    _patch_live_runtime(
        monkeypatch,
        EmptyAdapter,
        provider=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider optional")),
    )

    artifact_store = SimpleNamespace(
        get_artifact=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("artifact meta down"))
    )
    no_runtime = _StateStore(SimpleNamespace(bar_time=60_000, state_data={"bar_index": 1}))
    runner = lr.LiveStrategyRunner(
        config=lr.RunnerConfig(lookback_bars=3),
        orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000, 120_000))),
        artifact_store=artifact_store,
        state_store=no_runtime,
    )
    assert runner._run_mini_backtest(_strategy(), 120_000) == []

    future = _StateStore(SimpleNamespace(bar_time=120_000, state_data={"runtime_state": {}, "bar_index": 0}))
    runner = lr.LiveStrategyRunner(
        orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000))),
        state_store=future,
    )
    assert runner._run_mini_backtest(_strategy(), 120_000) == []

    bad_index = _StateStore(SimpleNamespace(bar_time=60_000, state_data={"runtime_state": {}, "bar_index": "bad"}))
    runner = lr.LiveStrategyRunner(
        config=lr.RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000, 120_000))),
        state_store=bad_index,
    )
    assert runner._run_mini_backtest(_strategy(), 180_000) == []
    assert bad_index.invalidated

    huge_index = _StateStore(SimpleNamespace(bar_time=60_000, state_data={"runtime_state": {}, "bar_index": 999}))
    runner = lr.LiveStrategyRunner(
        config=lr.RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000, 120_000))),
        state_store=huge_index,
    )
    assert runner._run_mini_backtest(_strategy(), 180_000) == []

    empty_bars = SimpleNamespace(query=_series((0,)).query, bars=[])
    runner = lr.LiveStrategyRunner(orchestrator=SimpleNamespace(load_bars=lambda query: empty_bars), state_store=None)
    assert runner._run_mini_backtest(_strategy(), 60_000) == []


def test_live_runner_mini_backtest_nonresume_error_and_resume_retry_empty(monkeypatch):
    class PlainErrorAdapter:
        def run(self, *args, **kwargs):
            raise RuntimeError("plain engine boom")

    _patch_live_runtime(monkeypatch, PlainErrorAdapter)
    store = _StateStore(SimpleNamespace(bar_time=60_000, state_data={"runtime_state": {}, "bar_index": 1}))
    runner = lr.LiveStrategyRunner(
        orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000, 120_000))),
        state_store=store,
    )
    assert runner._run_mini_backtest(_strategy(), 180_000) is None

    class ResumeErrorThenUnusedAdapter:
        calls = 0

        def run(self, *args, **kwargs):
            type(self).calls += 1
            if kwargs.get("resume_state") is not None and type(self).calls == 1:
                raise RuntimeError("resume config hash mismatch")
            return SimpleNamespace(raw_result=SimpleNamespace(trades=[], order_lifecycle=[]), resume_state=None)

    _patch_live_runtime(monkeypatch, ResumeErrorThenUnusedAdapter)

    class SwitchingOrchestrator:
        def __init__(self):
            self.calls = 0

        def load_bars(self, query):
            self.calls += 1
            if self.calls == 1:
                return _series((0, 60_000, 120_000))
            return SimpleNamespace(query=query, bars=[])

    store = _StateStore(SimpleNamespace(bar_time=60_000, state_data={"runtime_state": {}, "bar_index": 1}))
    runner = lr.LiveStrategyRunner(orchestrator=SwitchingOrchestrator(), state_store=store)
    assert runner._run_mini_backtest(_strategy(), 180_000) == []
    assert store.invalidated


def test_accounts_data_route_validation_refresh_delete_and_backfill(monkeypatch):
    state = SimpleNamespace()
    monkeypatch.setattr(ad, "_series_by_id", lambda state: {"sid": {"id": "sid", "latest_ms": None}})
    with pytest.raises(HTTPException) as no_latest:
        asyncio.run(ad.refresh_data_series("sid", state))
    assert no_latest.value.status_code == 400

    actual_series = {
        "id": "sid",
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "earliest_ms": 0,
        "latest_ms": 10**15,
        "ranges": [{"from_ms": 0, "to_ms": 10**15}],
        "status": "actual",
    }
    monkeypatch.setattr(ad, "_series_by_id", lambda state: {"sid": actual_series})
    refreshed = asyncio.run(ad.refresh_data_series("sid", SimpleNamespace(orchestrator=None)))
    assert refreshed["status"] == "actual" and refreshed["bars_loaded"] == 0

    class OrderStorage:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, tuple(params)))
            return _Cursor(rows=[])

    storage = OrderStorage()
    deleted = asyncio.run(
        ad.delete_data_orders(strategy_id="s1", status="filled", state=SimpleNamespace(storage=storage))
    )
    assert deleted["orders_deleted"] == 0
    assert "strategy_id = ?" in storage.calls[0][0] and storage.calls[0][1] == ("s1", "filled")

    with pytest.raises(HTTPException) as bad_backfill:
        asyncio.run(
            ad.data_backfill(
                DataBackfillRequest(symbol="BTCUSDT", timeframe="1m", from_time="2", to_time="1"),
                BackgroundTasks(),
                SimpleNamespace(),
            )
        )
    assert bad_backfill.value.status_code == 400

    payload = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "from_time": 0,
        "to_time": 120_000,
    }

    class Orchestrator:
        def load_bars(self, query, progress_callback=None):
            self.query = query
            return SimpleNamespace(query=query, bars=[object(), object()], coverage=SimpleNamespace(is_complete=True))

    progress = []
    with monkeypatch.context() as m:
        m.setattr(ad, "_stored_ranges_cover_request", lambda payload, state: (False, 0))
        m.setattr(ad, "_store_backfill_series", lambda state, series: (len(series.bars), 1))
        result = ad._run_data_backfill_sync(
            payload, SimpleNamespace(orchestrator=Orchestrator()), lambda *args: progress.append(args)
        )
    assert result == {"bars_loaded": 2, "skipped_existing": 1, "coverage_complete": True}
    assert progress and progress[-1][-1] == "write"


def test_accounts_data_inventory_range_store_and_delete_edges(monkeypatch, tmp_path):
    with monkeypatch.context() as m:
        m.setattr(ad, "_merge_persistent_cache_groups", lambda groups: None)
        m.setattr(ad, "_merge_marketdata_segment_groups", lambda state, groups: None)
        m.setattr(ad, "_merge_candle_manifest_groups", lambda state, groups: None)
        assert ad._stored_ranges_cover_request(
            {
                "exchange": "binance",
                "market_type": "spot",
                "symbol": "BTCUSDT",
                "timeframe": "1m",
                "from_time": 0,
                "to_time": 60_000,
            },
            SimpleNamespace(),
        ) == (False, 0)

    assert ad._ranges_cover_request([], "1m", 5, 5) is False
    assert ad._ranges_cover_request([{"from_ms": None, "to_ms": 1}, {"from_ms": 10, "to_ms": 10}], "1m", 0, 60_000) is False
    assert ad._coalesce_ranges([{"from_ms": 10, "to_ms": 0, "rows": 5}], "1m") == []
    assert ad._estimate_unique_bars([{"from_ms": 10, "to_ms": 0, "rows": 7}], "1m") == 7
    assert ad._dir_size(tmp_path / "missing") == 0

    no_bar_state = SimpleNamespace(orchestrator=SimpleNamespace(load_bars=lambda query: _series((0,))))
    assert ad._store_backfill_series(no_bar_state, SimpleNamespace(bars=[])) == (0, 0)
    existing = _series((0, 60_000))
    existing_state = SimpleNamespace(orchestrator=SimpleNamespace(load_bars=lambda query: existing))
    assert ad._store_backfill_series(existing_state, existing) == (0, len(existing.bars))

    cache_dir = tmp_path / "persistent-cache"
    cache_dir.mkdir()
    (cache_dir / "bad.json").write_text(json.dumps({"key": {"instrument": {"exchange": "binance"}}, "rows": 1}), encoding="utf-8")
    monkeypatch.setattr(ad, "default_cache_dir", lambda: cache_dir)
    groups = {}
    ad._merge_persistent_cache_groups(groups)
    assert groups == {}

    root = tmp_path / "marketdata"
    root.mkdir()
    (root / "index.sqlite").write_text("not sqlite", encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(ad, "_marketdata_store_root", lambda state: root)
        m.setattr(ad.sqlite3, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("connect bad")))
        ad._merge_marketdata_segment_groups(SimpleNamespace(), {})

    import marketdata_provider.contracts as contracts

    with monkeypatch.context() as m:
        m.setattr(contracts, "parse_timeframe", lambda value: (_ for _ in ()).throw(RuntimeError("bad timeframe")))
        assert ad._freshness_status(0, "bad") == "stale"

    delete_root = tmp_path / "delete-marketdata"
    source_dir = ad._marketdata_segment_dir(delete_root, "binance", "spot", "BTCUSDT", "1m", "trade_kline")
    source_dir.mkdir(parents=True)
    (source_dir / "part.parquet").write_text("x", encoding="utf-8")
    delete_root.mkdir(parents=True, exist_ok=True)
    (delete_root / "index.sqlite").write_text("index", encoding="utf-8")
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        m.setattr(ad, "_marketdata_store_root", lambda state: delete_root)
        m.setattr(ad.time, "time", lambda: 123.0)
        m.setattr(ad.sqlite3, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("delete index bad")))
        trash_dir = tmp_path / ".openpine" / "trash" / "marketdata-store-123000"
        (trash_dir / "timeframe=1m").mkdir(parents=True)
        deleted = ad._delete_marketdata_segment_series(
            SimpleNamespace(),
            {
                "exchange": "binance",
                "market_type": "spot",
                "symbol": "BTCUSDT",
                "timeframe": "1m",
                "source_kinds": ["trade_kline"],
            },
        )
    assert deleted == 1
    assert (tmp_path / ".openpine" / "trash" / "marketdata-store-123000" / "timeframe=1m-123000").exists()
