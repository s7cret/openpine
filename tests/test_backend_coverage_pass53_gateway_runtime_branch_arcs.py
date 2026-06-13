from __future__ import annotations

import asyncio
import ctypes
import inspect
import json
import queue
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import BackgroundTasks, WebSocketDisconnect
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.gateway import live_runner as lr
from openpine.gateway import server
from openpine.gateway.routes import accounts_data as ad
from openpine.gateway.routes import backtest as bt
from openpine.gateway.routes import dashboard, events, pine_ops, pine_sources, strategies
from openpine.gateway.schemas import (
    BacktestEstimateResponse,
    BacktestRunRequest,
    CompareTvRequest,
    PineSourceCreate,
    PineSourceUpdate,
)
from openpine.runtime import engine as runtime_engine


class _Cursor:
    def __init__(self, *, rows=(), one=None):
        self._rows = list(rows)
        self._one = one

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._rows[0] if self._rows else None


class _FakeWS:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.progress: dict[str, object] | None = None
        self.broadcasts: list[str] = []

    def update_progress(self, operation_id, domain, status, pct, message, detail=None):
        event = {
            "operation_id": operation_id,
            "domain": domain,
            "status": status,
            "pct": pct,
            "message": message,
            "detail": detail,
        }
        self.progress = event
        self.events.append(event)

    async def broadcast_progress(self, operation_id):
        self.broadcasts.append(operation_id)

    def get_progress(self, operation_id):
        return self.progress


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.executed.append((str(sql), tuple(params)))
        return _Cursor()

    def commit(self) -> None:
        self.commits += 1


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
        mode="paper",
        enabled=True,
        status="running",
        created_at=1,
        updated_at=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close + 1, close - 1, close, 10.0, True)


def _series(times=(0, 60_000)) -> BarSeries:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    bars = tuple(_bar(t, float(i + 1)) for i, t in enumerate(times))
    end_ms = (max(times) + 60_000) if times else 60_000
    query = BarQuery(
        inst,
        tf,
        min(times) if times else 0,
        end_ms,
        gap_policy="allow_with_metadata",
    )
    coverage = CoverageReport(
        query.start_ms,
        query.end_ms,
        bars[0].time if bars else None,
        bars[-1].time_close if bars else None,
        source_mix=("unit",),
    )
    return BarSeries(query, bars, coverage)


def _force_fast_local(frame, name: str, value: object) -> None:
    frame.f_locals[name] = value
    ctypes.pythonapi.PyFrame_LocalsToFast(ctypes.py_object(frame), ctypes.c_int(1))


def test_backtest_worker_queue_edges_without_progress_callbacks(monkeypatch):
    class Adapter:
        pass

    class FakeQueue:
        def __init__(self, get_items=(), nowait_items=()):
            self.get_items = list(get_items)
            self.nowait_items = list(nowait_items)
            self.closed = False

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
            self.closed = True

        def cancel_join_thread(self):
            pass

    class FakeProc:
        def __init__(self, *, alive=(False,), exitcode=0):
            self.alive = list(alive)
            self.exitcode = exitcode

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

    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(
            FakeQueue([("progress", 1, 2), ("ok", "progress-without-callback")]),
            FakeProc(alive=(True, True)),
        ),
    )
    assert (
        bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None)
        == "progress-without-callback"
    )

    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(
            FakeQueue([queue.Empty], [("progress", 2, 4), ("ok", "late-no-callback")]),
            FakeProc(alive=(False, False)),
        ),
    )
    assert bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None) == "late-no-callback"

    class ExitBeforeLoopProc(FakeProc):
        def is_alive(self):
            frame = inspect.currentframe().f_back
            _force_fast_local(frame, "final", ("ok", "forced-loop-exit"))
            return False

    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(FakeQueue(), ExitBeforeLoopProc(alive=())),
    )
    assert bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None) == "forced-loop-exit"


def test_backtest_routes_and_background_remaining_branches(monkeypatch):
    class Store:
        def __init__(self) -> None:
            self.saved: list[dict[str, object]] = []
            self.created: list[object] = []

        def create_run(self, request):
            self.created.append(request)
            return "run-created"

        def save_result(self, **kwargs):
            self.saved.append(kwargs)

        def mark_failed(self, run_id, message):
            raise AssertionError(f"unexpected failure: {message}")

        def mark_cancelled(self, run_id, message):
            raise AssertionError(f"unexpected cancellation: {message}")

        def get_run(self, run_id):
            return _strategy(run_id=run_id) if False else SimpleNamespace(
                run_id=run_id,
                strategy_id="s1",
                status="done",
                started_at=10,
                finished_at=20,
                symbol="BTCUSDT",
                timeframe="1m",
                from_time=0,
                to_time=60_000,
            )

        def list_runs(self, strategy_id, limit=50):
            return [
                SimpleNamespace(
                    run_id="other",
                    strategy_id=strategy_id,
                    started_at=1,
                    status="done",
                    finished_at=2,
                    symbol="BTCUSDT",
                    timeframe="1m",
                    from_time=0,
                    to_time=1,
                )
            ]

        def get_metrics(self, run_id):
            return {}

        def list_trades(self, run_id):
            return []

        def list_artifacts(self, run_id):
            return []

    ws = _FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    monkeypatch.setattr(bt, "_parse_date_ms", lambda value: int(value))
    monkeypatch.setattr(
        bt,
        "_estimate_backtest_market_data",
        lambda strategy, from_ms, to_ms: BacktestEstimateResponse(
            strategy_id=strategy.strategy_id,
            symbol=strategy.symbol.upper(),
            timeframe=strategy.timeframe,
            requested_from=from_ms,
            requested_to=to_ms,
            effective_from=from_ms,
            effective_to=to_ms,
            earliest_available=from_ms,
            adjusted=False,
            estimated_bars=2,
            estimated_pages=1,
        ),
    )
    store = Store()
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy(strategy_id=strategy_id)),
        backtest_store=store,
        backtest_cancel_requests=set(),
    )

    response = asyncio.run(
        bt.run_backtest(
            BacktestRunRequest(strategy_id="s1", from_time="1", to_time="2"),
            BackgroundTasks(),
            state=cast(Any, state),
        )
    )
    assert response.run_id == "run-created"
    assert ws.events[-1]["message"] == "Backtest queued"

    import openpine.data.direct_data_provider as direct_data_provider

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    captured: dict[str, object] = {}

    def run_in_process(adapter, strategy_class, bars, config, params, runtime_data_provider, progress_callback=None):
        captured["params"] = params
        return SimpleNamespace(raw_result=SimpleNamespace(trades=[], equity_curve=[]), bars_processed=len(bars))

    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", lambda *a, **k: type("Generated", (), {}))
    monkeypatch.setattr(runtime_engine, "BacktestRunConfig", FakeConfig)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", lambda: object())
    monkeypatch.setattr(direct_data_provider, "DirectBinanceDataProvider", lambda *a, **k: object())
    monkeypatch.setattr(bt, "_run_backtest_in_process", run_in_process)
    monkeypatch.setattr(bt, "_save_backtest_data_fingerprint", lambda *a, **k: None)
    monkeypatch.setattr(
        bt,
        "_estimate_backtest_market_data",
        lambda strategy, from_ms, to_ms: SimpleNamespace(
            estimated_bars=2,
            estimated_pages=1,
            effective_from=from_ms,
            earliest_available=from_ms,
            adjusted=False,
        ),
    )
    bg_state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy(strategy_id=strategy_id, params_json="")),
        artifact_store=SimpleNamespace(get_artifact=lambda *a, **k: {"compile_meta": {}}),
        orchestrator=SimpleNamespace(load_bars=lambda query, progress_callback=None: _series((0, 60_000))),
        backtest_store=store,
        backtest_cancel_requests=set(),
        storage=SimpleNamespace(),
    )
    asyncio.run(bt._run_backtest_background(bg_state, "s1", "run-bg", 0, 120_000, None, 0, False))
    assert captured["params"] == {}
    assert store.saved[-1]["run_id"] == "run-bg"

    detail = asyncio.run(bt.get_run("run-with-no-version-match", state=cast(Any, state)))
    assert detail.version == 1

    monkeypatch.setattr(
        bt,
        "ws_manager",
        SimpleNamespace(
            get_progress=lambda run_id: {
                "operation_id": run_id,
                "status": "running",
                "pct": 0.42,
                "message": "still working",
                "detail": {},
            }
        ),
    )
    progress = asyncio.run(bt.get_progress("run-progress"))
    assert progress is not None
    assert progress.bars_processed == 0 and progress.total_bars == 0

    exported = asyncio.run(bt.export_run("run-export", state=cast(Any, state)))
    assert "metrics" not in exported
    assert exported["trades"] == [] and exported["artifacts"] == []


def test_accounts_data_series_and_delete_branch_arcs(monkeypatch, tmp_path: Path):
    key = ("binance", "spot", "BTCUSDT", "trade", "1m")
    entry = ad._series_entry({}, key)
    ad._extend_series(entry, 1, None, 10, 3, "cache", "m1")
    ad._extend_series(entry, 1, 5, None, 4, "cache", "m2")
    assert entry["earliest_ms"] == 5
    assert entry["latest_ms"] == 10

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "skip.json").write_text(
        json.dumps(
            {
                "key": {
                    "instrument": {"exchange": "coinbase", "market": "spot", "symbol": "ETHUSDT"},
                    "timeframe": "1m",
                }
            }
        ),
        encoding="utf-8",
    )
    matching_meta = cache_dir / "match.json"
    matching_meta.write_text(
        json.dumps(
            {
                "key": {
                    "instrument": {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
                    "timeframe": "1m",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ad, "default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(ad.time, "time", lambda: 123.0)
    deleted = ad._delete_persistent_cache_series(
        {"exchange": "binance", "market_type": "spot", "symbol": "BTCUSDT", "timeframe": "1m"}
    )
    assert deleted == 1
    assert not matching_meta.exists()
    assert (cache_dir / "skip.json").exists()

    class ManifestStorage:
        def __init__(self) -> None:
            self.deleted: list[tuple[object, ...]] = []

        def execute(self, sql, params=()):
            if str(sql).lstrip().startswith("SELECT"):
                return _Cursor(rows=[("manifest-empty", ""), ("manifest-missing", str(tmp_path / "missing.parquet"))])
            self.deleted.append(tuple(params))
            return _Cursor()

        def transaction(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    manifest_state = SimpleNamespace(storage=ManifestStorage())
    deleted_manifests = ad._delete_candle_manifest_series(
        cast(Any, manifest_state),
        {
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTCUSDT",
            "price_type": "trade",
            "timeframe": "1m",
        },
    )
    assert deleted_manifests == 2
    assert manifest_state.storage.deleted == [("manifest-empty",), ("manifest-missing",)]


def _patch_live_runtime(monkeypatch, adapter_cls):
    import openpine.data.direct_data_provider as direct_data_provider

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", lambda *a, **k: type("Generated", (), {}))
    monkeypatch.setattr(runtime_engine, "BacktestRunConfig", FakeConfig)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", adapter_cls)
    monkeypatch.setattr(direct_data_provider, "DirectBinanceDataProvider", lambda *a, **k: object())


class _StateStore:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot
        self.saved: list[dict[str, object]] = []

    def latest_snapshot_metadata(self, *args, **kwargs):
        return self.snapshot

    def load_latest_compatible(self, *args, **kwargs):
        return self.snapshot

    def save_runtime_snapshot(self, **kwargs):
        self.saved.append(kwargs)

    def mark_invalid(self, *args, **kwargs):
        pass


def test_live_runner_mini_backtest_artifact_fallback_and_risk_branches(monkeypatch):
    class EmptyAdapter:
        def run(self, *args, **kwargs):
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state={"runtime_state": {}, "bar_index": 1},
            )

    _patch_live_runtime(monkeypatch, EmptyAdapter)
    runner = lr.LiveStrategyRunner(
        orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000))),
        artifact_store=SimpleNamespace(get_artifact=lambda *a, **k: None),
        state_store=_StateStore(),
    )
    assert runner._run_mini_backtest(_strategy(), 60_000) == []

    snapshot = SimpleNamespace(bar_time=0, state_data={"runtime_state": {}, "bar_index": 0})

    class EmptyOnSecondLoad:
        def __init__(self) -> None:
            self.calls = 0

        def load_bars(self, query):
            self.calls += 1
            return _series((0, 60_000)) if self.calls == 1 else SimpleNamespace(query=query, bars=[])

    runner = lr.LiveStrategyRunner(
        orchestrator=EmptyOnSecondLoad(),
        artifact_store=SimpleNamespace(get_artifact=lambda *a, **k: {}),
        state_store=_StateStore(snapshot),
    )
    assert runner._run_mini_backtest(_strategy(), 120_000) == []

    class NonEmptyFallbackLoad:
        def load_bars(self, query):
            return _series((0, 60_000))

    runner = lr.LiveStrategyRunner(
        orchestrator=NonEmptyFallbackLoad(),
        artifact_store=SimpleNamespace(get_artifact=lambda *a, **k: {}),
        state_store=_StateStore(snapshot),
    )
    assert runner._run_mini_backtest(_strategy(), 120_000) == []

    monkeypatch.setattr(lr.LiveStrategyRunner, "_strategy_risk_percents", lambda self, strategy: (10.0, 5.0))
    orders = [
        {"entry_price": 100, "side": "buy", "take_profit_price": 111, "stop_price": 99},
        {"entry_price": 200, "side": "sell", "take_profit_price": 180, "stop_price": 205},
    ]
    lr.LiveStrategyRunner(state_store=_StateStore())._attach_risk_prices(_strategy(), orders)
    assert orders[0]["take_profit_price"] == 111
    assert orders[1]["stop_price"] == 205


def test_runtime_engine_generated_module_adapter_and_progress_false_branch(monkeypatch, tmp_path: Path):
    generated = tmp_path / "generated_strategy.py"
    generated.write_text("label = 'custom-label'\n", encoding="utf-8")
    module = runtime_engine._load_generated_module(generated, "src-1", "art-1")
    assert module.label == "custom-label"
    assert module.line.anything == "line.anything"

    captured: dict[str, object] = {}

    class FakeBacktestConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeBacktestEngine:
        def __init__(self, config):
            self.config = config

        def run(self, strategy_class, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status="ok", resume_state="resume")

    adapter = object.__new__(runtime_engine.BacktestEngineAdapter)
    adapter._module = SimpleNamespace(BacktestConfig=FakeBacktestConfig, BacktestEngine=FakeBacktestEngine)
    adapter._to_engine_bar = lambda bar: ("engine-bar", bar.time)

    class Strategy:
        pass

    result = adapter.run(
        Strategy,
        [_bar(0)],
        runtime_engine.BacktestRunConfig(symbol="BTCUSDT", timeframe="1m", start_time=0, end_time=60_000),
        params=None,
        progress_callback=None,
        runtime_data_provider=None,
    )
    assert result.bars_processed == 1
    assert captured["callbacks"] is None
    assert "progress_callback" not in captured["runtime_kwargs"]
    assert not hasattr(Strategy, "runtime_data_provider")

    callbacks_mod = types.ModuleType("backtest_engine.models.callbacks")

    class FakeBacktestCallbacks:
        def __init__(self, on_bar_end):
            self.on_bar_end = on_bar_end

    callbacks_mod.BacktestCallbacks = FakeBacktestCallbacks
    monkeypatch.setitem(sys.modules, "backtest_engine", types.ModuleType("backtest_engine"))
    monkeypatch.setitem(sys.modules, "backtest_engine.models", types.ModuleType("backtest_engine.models"))
    monkeypatch.setitem(sys.modules, "backtest_engine.models.callbacks", callbacks_mod)
    monkeypatch.setattr(runtime_engine.time, "perf_counter", lambda: 0.1)
    progress_calls: list[tuple[int, int]] = []
    callbacks = runtime_engine.BacktestEngineAdapter._progress_callbacks(
        lambda done, total: progress_calls.append((done, total)), total=5000
    )
    callbacks.on_bar_end(None, 0, None)
    assert progress_calls == []


def test_strategies_update_and_compare_tv_remaining_branches(tmp_path: Path):
    class Registry:
        def __init__(self) -> None:
            self.current = _strategy()
            self._conn = _Conn()
            self.mode_updates: list[str] = []

        def get_strategy(self, strategy_id):
            return self.current

        def update_mode(self, strategy_id, mode):
            self.mode_updates.append(mode)
            self.current.mode = mode

        def set_enabled(self, strategy_id, enabled):
            self.current.enabled = enabled

        def update_status(self, strategy_id, status):
            self.current.status = status

    class FakeBody:
        def model_dump(self, exclude_unset=True):
            return {"mode": "live", "symbol": "ETHUSDT"}

    registry = Registry()
    response = asyncio.run(
        strategies.update_strategy(
            "s1", cast(Any, FakeBody()), state=cast(Any, SimpleNamespace(strategy_registry=registry))
        )
    )
    assert response.mode == "live"
    assert registry.mode_updates == ["live"]
    assert "params_hash" not in registry._conn.executed[0][0]

    openpine_csv = tmp_path / "openpine.csv"
    tv_csv = tmp_path / "tv.csv"
    openpine_csv.write_text(
        "time,bar_index,a,b,c\n1000,0,,,\n2000,0,1,1,1\n",
        encoding="utf-8",
    )
    tv_csv.write_text(
        "time,bar_index,a,b,c\n1000,0,,,\n2000,0,3,2,2\n",
        encoding="utf-8",
    )
    compared = asyncio.run(
        strategies.strategy_compare_tv(
            "s1",
            CompareTvRequest(
                openpine_plots_path=str(openpine_csv),
                tv_chart_path=str(tv_csv),
                abs_tol=0.0,
                include_base_columns=False,
            ),
            state=cast(Any, SimpleNamespace()),
        )
    )
    assert compared["status"] == "mismatch"
    assert compared["mismatch_cells"] == 3
    assert compared["max_abs_delta"] == 2.0


def test_pine_sources_create_update_delete_and_preview_missing_paths(tmp_path: Path):
    src = SimpleNamespace(
        id="pine-1",
        name="name",
        source_type="strategy",
        version="v1",
        source_text="//@version=5",
        source_hash="hash",
        active_artifact_id=None,
        created_at=1,
        updated_at=2,
    )

    class CreateRegistry:
        def get_source(self, name):
            return None

        def add_source(self, source_text, name):
            src.name = name
            src.source_text = source_text
            return src

    created = asyncio.run(
        pine_sources.create_source(
            PineSourceCreate(name="new-name", source_text="plot(close)", source_type="indicator"),
            registry=cast(Any, CreateRegistry()),
        )
    )
    assert created.name == "new-name" and created.source_type == "indicator"

    class UpdateRegistry:
        def __init__(self) -> None:
            self._conn = _Conn()
            self._mem: dict[str, object] = {}

        def get_source(self, source_id):
            return src

    update_registry = UpdateRegistry()
    updated = asyncio.run(
        pine_sources.update_source(
            "pine-1", PineSourceUpdate(name="renamed"), registry=cast(Any, update_registry)
        )
    )
    assert updated.name == "renamed"
    assert updated.source_type == "indicator"

    class PineRegistry:
        def __init__(self) -> None:
            self.removed: list[str] = []

        def get_source(self, source_id):
            return src

        def remove_source(self, source_id):
            self.removed.append(source_id)

    storage = _Conn()
    pine_registry = PineRegistry()
    missing_source_dir = tmp_path / "does-not-exist"
    state = SimpleNamespace(
        pine_registry=pine_registry,
        storage=storage,
        artifact_store=SimpleNamespace(_source_dir=lambda source_id: missing_source_dir),
    )
    asyncio.run(pine_sources.delete_source("pine-1", state=cast(Any, state)))
    assert pine_registry.removed == ["pine-1"]
    assert storage.commits == 1

    preview = asyncio.run(pine_sources.delete_source_preview("pine-1", state=cast(Any, state)))
    assert preview["resources"]["artifact_files"] == 0


def test_dashboard_empty_last_bar_and_strategy_health_branches(monkeypatch):
    enabled = [_strategy(strategy_id="s1", symbol="BTCUSDT"), _strategy(strategy_id="s2", symbol="ETHUSDT")]

    class Registry:
        def list_strategies(self):
            return enabled

    class Storage:
        def execute(self, sql, params=()):
            sql_text = str(sql)
            if sql_text.startswith("PRAGMA"):
                return _Cursor(rows=[(0, "created_at")])
            if "SELECT MAX" in sql_text:
                return _Cursor(one=(None,))
            if "FROM backtest_runs" in sql_text:
                return _Cursor(rows=[])
            if "FROM orders" in sql_text and "WHERE strategy_id" in sql_text:
                return _Cursor(one=None)
            return _Cursor(rows=[], one=(0,))

    class Orchestrator:
        def latest_bar_time(self, query):
            return None

        def load_bars(self, query):
            return SimpleNamespace(bars=[])

    state = SimpleNamespace(
        strategy_registry=Registry(),
        scheduler=SimpleNamespace(list_jobs=lambda: []),
        storage=Storage(),
        orchestrator=Orchestrator(),
        _fetcher=None,
        _live_runner=None,
        _background_worker_process=None,
        _risk_kill_switch=[False],
        _startup_time=1000.0,
    )
    monkeypatch.setattr(dashboard.time, "time", lambda: 1001.0)

    response = asyncio.run(dashboard.dashboard(state=cast(Any, state)))
    assert response.last_bar_update is None
    assert response.strategies[0].health["last_order"] is None
    assert response.strategies[0].health["last_bar_time"] is None


def test_pine_ops_compile_list_artifacts_and_inspect_branches(monkeypatch, tmp_path: Path):
    ws = _FakeWS()
    monkeypatch.setattr(pine_ops, "ws_manager", ws)

    pine2ast = types.ModuleType("pine2ast")

    class ParseOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    pine2ast.ParseOptions = ParseOptions
    pine2ast.parse_code = lambda source_text, options=None: SimpleNamespace(ok=True, ast={"kind": "root"}, diagnostics=[])
    pine2ast.ast_to_dict = lambda ast: {}
    pine2ast.ast_to_json = lambda ast: "{}"

    ast2python = types.ModuleType("ast2python")
    warning_diag = SimpleNamespace(message="only a warning", severity=SimpleNamespace(value="warning"))
    ast2python.translate_ast = lambda ast_dict, module_name: SimpleNamespace(
        diagnostics=[warning_diag], code="class GeneratedStrategy: pass\n", metadata={"ok": True}
    )
    monkeypatch.setitem(sys.modules, "pine2ast", pine2ast)
    monkeypatch.setitem(sys.modules, "ast2python", ast2python)

    class PineRegistry:
        def __init__(self) -> None:
            self.active: list[tuple[str, str]] = []

        def get_source(self, source_id):
            return SimpleNamespace(id=source_id, name="Source", source_text="plot(close)")

        def set_active_artifact(self, source_id, artifact_id):
            self.active.append((source_id, artifact_id))

    class ArtifactStore:
        def __init__(self) -> None:
            self.saved: list[dict[str, object]] = []
            self._root = tmp_path / "artifacts"

        def save_artifact(self, **kwargs):
            self.saved.append(kwargs)

        def get_artifact(self, artifact_id, source_id):
            artifact_dir = self._root / source_id / artifact_id
            artifact_dir.mkdir(exist_ok=True)
            (artifact_dir / "diagnostics.log").write_text("diag text", encoding="utf-8")
            return {"artifact_dir": artifact_dir, "compile_meta": {"compile_status": "OK"}}

    artifact_store = ArtifactStore()
    registry = PineRegistry()
    state = SimpleNamespace(pine_registry=registry, artifact_store=artifact_store)

    tasks: list[tuple[object, tuple, dict]] = []

    class CapturingTasks:
        def add_task(self, func, *args, **kwargs):
            tasks.append((func, args, kwargs))

    response = asyncio.run(pine_ops.compile_pine("pine-1", cast(Any, CapturingTasks()), state=cast(Any, state)))
    assert response["status"] == "queued"
    func, args, kwargs = tasks.pop()
    asyncio.run(func(*args, **kwargs))
    assert artifact_store.saved
    assert registry.active[0][0] == "pine-1"
    assert ws.events[-1]["status"] == "completed"

    source_dir = artifact_store._root / "pine-1"
    (source_dir / "a-empty").mkdir(parents=True)
    meta_dir = source_dir / "b-meta"
    meta_dir.mkdir()
    (meta_dir / "compile_meta.json").write_text(json.dumps({"compile_status": "OK"}), encoding="utf-8")
    artifacts = asyncio.run(pine_ops.list_artifacts("pine-1", state=cast(Any, state)))
    assert [item["artifact_id"] for item in artifacts] == ["b-meta"]

    inspected = asyncio.run(pine_ops.inspect_artifact("pine-1", "b-meta", state=cast(Any, state)))
    assert "generated_python_lines" not in inspected
    assert inspected["diagnostics"] == "diag text"


def test_server_lifespan_no_stuck_runs_and_worker_exits_without_terminate(monkeypatch):
    class Storage:
        def execute(self, sql, params=()):
            return _Cursor(rows=[])

        def commit(self):
            raise AssertionError("no stuck rows should be committed")

    class FakeGatewayState:
        def __init__(self) -> None:
            self.config = SimpleNamespace(sqlite_path=Path("unit.sqlite"), live_enabled=False)
            self.storage = Storage()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class StopEvent:
        def __init__(self) -> None:
            self.set_called = False

        def set(self):
            self.set_called = True

    class FakeProcess:
        instances: list["FakeProcess"] = []

        def __init__(self, *, target, args, name, daemon) -> None:
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.pid = 1234
            self.started = False
            self.join_timeouts: list[object] = []
            self.terminated = False
            FakeProcess.instances.append(self)

        def start(self):
            self.started = True

        def join(self, timeout=None):
            self.join_timeouts.append(timeout)

        def is_alive(self):
            return False

        def terminate(self):
            self.terminated = True

    class Context:
        def __init__(self) -> None:
            self.event = StopEvent()

        def Event(self):
            return self.event

        Process = FakeProcess

    fake_context = Context()
    monkeypatch.setattr(server, "GatewayState", FakeGatewayState)
    monkeypatch.setattr(server.mp, "get_context", lambda method: fake_context)
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "0")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "0")

    async def run_lifespan():
        app: Any = SimpleNamespace(state=SimpleNamespace())
        async with server.lifespan(cast(Any, app)):
            assert app.state.gateway._background_worker_process is FakeProcess.instances[0]
            assert FakeProcess.instances[0].started is True
        return app.state.gateway

    state = asyncio.run(run_lifespan())
    assert fake_context.event.set_called is True
    assert FakeProcess.instances[0].join_timeouts == [10]
    assert FakeProcess.instances[0].terminated is False
    assert state.closed is True


def test_events_websocket_acknowledges_then_disconnects(monkeypatch):
    class Manager:
        def __init__(self) -> None:
            self.disconnected: list[str] = []

        async def connect(self, ws):
            return "client-1"

        async def disconnect(self, client_id):
            self.disconnected.append(client_id)

    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages = ["subscribe"]
            self.sent: list[dict[str, object]] = []

        async def receive_text(self):
            if self.messages:
                return self.messages.pop(0)
            raise WebSocketDisconnect()

        async def send_json(self, payload):
            self.sent.append(payload)

    manager = Manager()
    ws = FakeWebSocket()
    monkeypatch.setattr(events, "ws_manager", manager)
    asyncio.run(events.websocket_events(cast(Any, ws)))
    assert ws.sent == [{"type": "ack", "data": "subscribe"}]
    assert manager.disconnected == ["client-1"]
