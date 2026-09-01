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

import pytest
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
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest, canonical_series


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
        semantic_profile="strict_5x",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close + 1, close - 1, close, 10.0, True)


def _series(times=(0, 60_000)):
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
    return canonical_series(BarSeries(query, bars, coverage))


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
            self.pid = 4242
            self.alive = list(alive)
            self.exitcode = exitcode

        def start(self):
            pass

        def is_alive(self):
            return self.alive.pop(0) if self.alive else False

        def join(self, timeout=None):
            pass

    class Ctx:
        class Receiver:
            def __init__(self, proc):
                self.proc = proc

            def recv(self):
                return (self.proc.pid, 7)

            def close(self):
                pass

        class Sender:
            def close(self):
                pass

        def __init__(self, q, proc):
            self.q = q
            self.proc = proc

        def Queue(self):
            return self.q

        def Event(self):
            return SimpleNamespace(is_set=lambda: True)

        def Pipe(self, duplex=False):
            assert duplex is False
            return self.Receiver(self.proc), self.Sender()

        def Process(self, **kwargs):
            return self.proc

    monkeypatch.setattr(bt, "_proc_identity", lambda pid: ("S", pid, 7))
    monkeypatch.setattr(bt, "_terminate_backtest_worker", lambda worker, timeout=3.0: True)
    monkeypatch.setattr(
        bt.mp,
        "get_context",
        lambda name: Ctx(
            FakeQueue([("progress", 1, 2), ("ok", "progress-without-callback")]),
            FakeProc(alive=(True, True)),
        ),
    )
    assert (
        bt._execute_backtest_run_in_thread("run", set(), Adapter(), object, [], object(), {}, None)
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
    assert bt._execute_backtest_run_in_thread("run", set(), Adapter(), object, [], object(), {}, None) == "late-no-callback"

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
    assert bt._execute_backtest_run_in_thread("run", set(), Adapter(), object, [], object(), {}, None) == "forced-loop-exit"




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
