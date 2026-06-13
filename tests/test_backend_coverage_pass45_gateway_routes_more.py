from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import dashboard, events, orders_positions, pine_ops, strategies, trading
from openpine.gateway.schemas import CompareTvRequest, LiveStartRequest, PaperStartRequest, StrategyCreate, StrategyMode, StrategyUpdate
from openpine.registry.strategies import StrategyInstance


def _strategy(strategy_id: str = "s1", *, status: str = "paused", enabled: bool = True, symbol: str = "BTCUSDT") -> StrategyInstance:
    return StrategyInstance(
        strategy_id=strategy_id,
        name=f"Strategy {strategy_id}",
        pine_id="pine",
        artifact_id="artifact",
        params_json='{"x":1}',
        params_hash="hash",
        symbol=symbol,
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode="paper",
        enabled=enabled,
        status=status,
        created_at=1,
        updated_at=2,
    )


class Cursor:
    def __init__(self, rows=(), one=None):
        self._rows = list(rows)
        self._one = one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._rows[0] if self._rows else None


class Registry:
    def __init__(self) -> None:
        self.items = {
            "s1": _strategy("s1", enabled=True),
            "s2": _strategy("s2", enabled=True, symbol="BTCUSDT"),
            "err": _strategy("err", status="error"),
        }
        self.calls: list[tuple[str, object, object | None]] = []
        self._conn = self

    def list_strategies(self):
        return list(self.items.values())

    def get_strategy(self, strategy_id):
        if strategy_id not in self.items:
            raise KeyError(strategy_id)
        return self.items[strategy_id]

    def create_strategy(self, **kwargs):
        s = _strategy("created")
        for key, value in kwargs.items():
            if hasattr(s, key):
                setattr(s, key, value)
        self.items[s.strategy_id] = s
        return s

    def set_enabled(self, strategy_id, enabled):
        self.calls.append(("enabled", strategy_id, enabled))
        self.items[strategy_id].enabled = enabled

    def update_mode(self, strategy_id, mode):
        self.calls.append(("mode", strategy_id, mode))
        self.items[strategy_id].mode = mode

    def update_status(self, strategy_id, status):
        self.calls.append(("status", strategy_id, status))
        self.items[strategy_id].status = status

    def execute(self, sql, values):
        strategy_id = values[-1]
        s = self.items[strategy_id]
        set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        fields = [part.split("=", 1)[0].strip() for part in set_clause.split(",")]
        for field, value in zip(fields, values[:-1]):
            if hasattr(s, field):
                setattr(s, field, value)
        return Cursor()

    def commit(self):
        return None

    def delete_strategy(self, strategy_id):
        if strategy_id not in self.items:
            raise KeyError(strategy_id)
        del self.items[strategy_id]


class Storage:
    def __init__(self) -> None:
        self.fail_positions = False
        self.fail_events = False
        self.fail_counts = False

    def execute(self, sql, params=()):
        text = " ".join(sql.split()).lower()
        if self.fail_events and "pragma table_info(events)" in text:
            raise RuntimeError("events schema gone")
        if "pragma table_info(events)" in text:
            return Cursor(rows=[(0, "event_id"), (1, "event_type"), (2, "payload"), (3, "timestamp_ms")])
        if "select max(timestamp_ms)" in text:
            return Cursor(one=(123456,))
        if "from events" in text:
            return Cursor(rows=[("e1", "order.filled", json.dumps({"strategy_id": "s1"}), 111)])
        if "select run_id from backtest_runs" in text:
            return Cursor(rows=[])
        if "from backtest_runs" in text:
            return Cursor(rows=[("r1", "s1", "success", 10, 20, 5, None), ("r2", "s2", "cancelled", 11, 21, 6, "x")])
        if "select count" in text:
            if self.fail_counts:
                raise RuntimeError("count failed")
            return Cursor(one=(0,))
        if "from orders where order_id" in text:
            return Cursor(one=None)
        if "from orders" in text:
            return Cursor(rows=[(
                "o1", "s1", "acct", "client", "BTCUSDT", "buy", "limit", 1.0,
                10.0, None, None, "open", 0.0, None, None, 1, 2,
            )])
        if "from strategy_positions where strategy_id" in text:
            if self.fail_positions:
                raise RuntimeError("positions failed")
            return Cursor(rows=[("s1", "BTCUSDT", "long", 1.0, 10.0, 2.0, 3.0, 1, 2)])
        if "from strategy_positions" in text:
            if self.fail_positions:
                raise RuntimeError("positions failed")
            return Cursor(rows=[("s1", "BTCUSDT", "long", 1.0, 10.0, 2.0, 3.0, 1, 2)])
        return Cursor(rows=[])


class Orchestrator:
    def __init__(self) -> None:
        self.latest_calls = 0

    def latest_bar_time(self, query):
        self.latest_calls += 1
        return 777000

    def load_bars(self, query):
        return SimpleNamespace(bars=[SimpleNamespace(time=0)])


class StateStore:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def load_snapshot(self, strategy_id):
        if self.fail:
            raise RuntimeError("snapshot failed")
        return SimpleNamespace(bar_time=500, state_data={"position": {"qty": 2.0, "side": "long"}})


class State(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(
            strategy_registry=Registry(),
            pine_registry=SimpleNamespace(get_source=lambda source_id: SimpleNamespace(source_id=source_id, name="source", source_text="//@version=6\nindicator('x')")),
            artifact_store=SimpleNamespace(_root=Path("/tmp/no-artifacts")),
            storage=Storage(),
            scheduler=SimpleNamespace(list_jobs=lambda: []),
            orchestrator=Orchestrator(),
            state_store=StateStore(),
            config=SimpleNamespace(live_enabled=False),
            _risk_kill_switch=[False],
            _startup_time=0.0,
        )


@pytest.mark.asyncio
async def test_dashboard_trading_orders_and_events_remaining_gateway_edges(monkeypatch):
    state = State()
    monkeypatch.setattr(dashboard.time, "time", lambda: 1_000_000.0)

    dash = await dashboard.dashboard(state)
    assert dash.last_event_time == 123456
    assert dash.last_bar_update == 777000
    assert dash.jobs.done == 1 and dash.jobs.failed == 1
    health = dashboard._strategy_health(state, state.strategy_registry.items["s1"])
    assert health["status"] == "stale"

    state.strategy_registry.items["s1"].status = "error"
    assert dashboard._strategy_health(state, state.strategy_registry.items["s1"])["status"] == "error"
    state.strategy_registry.items["s1"].status = "paused"
    state._background_worker_process = SimpleNamespace(is_alive=lambda: True)
    assert dashboard._strategy_health(state, state.strategy_registry.items["s1"])["runner_alive"] is True
    state.storage.fail_events = True
    assert await events.list_events(state=state) == []
    state.storage.fail_events = False
    listed_events = await events.list_events(event_type="order.filled", strategy_id="s1", state=state)
    assert listed_events[0].payload["strategy_id"] == "s1"

    with pytest.raises(HTTPException):
        await trading.start_paper(PaperStartRequest(strategy_id="missing"), state)
    with pytest.raises(HTTPException):
        await trading.stop_paper(PaperStartRequest(strategy_id="missing"), state)
    with pytest.raises(HTTPException):
        await trading.start_live(LiveStartRequest(strategy_id="s1"), state)
    state.config.live_enabled = True
    with pytest.raises(HTTPException):
        await trading.start_live(LiveStartRequest(strategy_id="missing"), state)
    state.strategy_registry.items["err"].status = "error"
    with pytest.raises(HTTPException):
        await trading.start_live(LiveStartRequest(strategy_id="err"), state)
    assert (await trading.get_trading_status("s1", state)).position_side == "long"
    state.state_store = StateStore(fail=True)
    assert (await trading.get_trading_status("s1", state)).position_side is None
    with pytest.raises(HTTPException):
        await trading.get_trading_status("missing", state)

    assert (await orders_positions.list_orders(state=state))[0]["order_id"] == "o1"
    with pytest.raises(HTTPException):
        await orders_positions.get_order("missing", state)
    state.storage.fail_positions = True
    assert await orders_positions.list_positions(state=state) == []
    result = await orders_positions.get_strategy_positions("s1", state)
    assert result["positions"] == [] and result["recent_trades"] == []


@pytest.mark.asyncio
async def test_strategy_route_uncovered_status_preview_and_compare_edges(tmp_path: Path):
    state = State()
    registry = state.strategy_registry

    class StatusUpdate:
        def model_dump(self, exclude_unset=True):
            return {"status": "running"}

    updated = await strategies.update_strategy("s1", StatusUpdate(), state)
    assert updated.status == "running"
    state.storage.fail_counts = True
    preview = await strategies.delete_strategy_preview("s1", state)
    assert preview["resources"]["orders"] == 0

    op = tmp_path / "op.csv"
    tv = tmp_path / "tv.csv"
    op.write_text("time,plot,bad\n1,2.0,nope\n2,3.0,4.0\n", encoding="utf-8")
    tv.write_text("time,plot,bad\n1,2.0,1.0\n2,3.5,4.0\n", encoding="utf-8")
    comp = await strategies.strategy_compare_tv(
        "s1",
        CompareTvRequest(openpine_plots_path=str(op), tv_chart_path=str(tv), abs_tol=0.1, include_base_columns=True),
        state,
    )
    assert comp["worst_column"] == "plot"
    broken = tmp_path / "broken.csv"
    broken.mkdir()
    with pytest.raises(HTTPException):
        await strategies.strategy_compare_tv(
            "s1",
            CompareTvRequest(openpine_plots_path=str(op), tv_chart_path=str(broken)),
            state,
        )

    created = await strategies.create_strategy(
        StrategyCreate(name="new", pine_id="pine", artifact_id="artifact", symbol="ETHUSDT", timeframe="5m", mode=StrategyMode.PAPER),
        SimpleNamespace(
            strategy_registry=registry,
            pine_registry=SimpleNamespace(get_source=lambda source_id: SimpleNamespace(id=source_id)),
            artifact_store=SimpleNamespace(get_artifact=lambda artifact_id, source_id: {"compile_meta": {"compile_status": "OK"}}),
        ),
    )
    assert created.strategy_id == "created"


class Background:
    def __init__(self) -> None:
        self.tasks = []

    def add_task(self, fn):
        self.tasks.append(fn)


class Progress:
    def __init__(self) -> None:
        self.updates = []
        self.broadcasts = []

    def update_progress(self, *args):
        self.updates.append(args)

    async def broadcast_progress(self, operation_id):
        self.broadcasts.append(operation_id)

    def get_progress(self, operation_id):
        return {"operation_id": operation_id, "status": "running"}


def _install_pine_modules(monkeypatch, *, parse_ok=True, translation_errors=False):
    pine2ast = ModuleType("pine2ast")

    class ParseOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    diag = SimpleNamespace(code="E", severity=SimpleNamespace(value="error"), message="boom")
    pine2ast.ParseOptions = ParseOptions
    pine2ast.parse_code = lambda source, options=None: SimpleNamespace(ok=parse_ok, diagnostics=[diag] if not parse_ok else [], ast=SimpleNamespace(node="ast"))
    pine2ast.ast_to_dict = lambda ast: {"ast": True}
    pine2ast.ast_to_json = lambda ast: "{}"

    ast2python = ModuleType("ast2python")
    trans_diag = SimpleNamespace(severity=SimpleNamespace(value="fatal"), message="bad translation")
    ast2python.translate_ast = lambda ast, module_name=None: SimpleNamespace(
        diagnostics=[trans_diag] if translation_errors else [],
        code="class GeneratedStrategy: pass\n",
        metadata={"ok": True},
    )
    monkeypatch.setitem(sys.modules, "pine2ast", pine2ast)
    monkeypatch.setitem(sys.modules, "ast2python", ast2python)


@pytest.mark.asyncio
async def test_pine_ops_compile_validate_artifacts_and_progress_edges(monkeypatch, tmp_path: Path):
    progress = Progress()
    monkeypatch.setattr(pine_ops, "ws_manager", progress)
    state = State()

    bg = Background()
    _install_pine_modules(monkeypatch, parse_ok=False)
    queued = await pine_ops.compile_pine("pine", bg, state)
    assert queued["status"] == "queued"
    await bg.tasks[0]()
    assert any(update[2] == "failed" for update in progress.updates)

    bg = Background()
    progress.updates.clear()
    _install_pine_modules(monkeypatch, parse_ok=True, translation_errors=True)
    await pine_ops.compile_pine("pine", bg, state)
    await bg.tasks[0]()
    assert any("Translation failed" in update[4] for update in progress.updates if len(update) > 4)

    class RaisingArtifactStore:
        _root = tmp_path / "artifacts"
        def save_artifact(self, **kwargs):
            raise RuntimeError("save failed")
        def get_artifact(self, artifact_id, source_id):
            raise FileNotFoundError(artifact_id)

    bg = Background()
    progress.updates.clear()
    _install_pine_modules(monkeypatch, parse_ok=True, translation_errors=False)
    state.artifact_store = RaisingArtifactStore()
    await pine_ops.compile_pine("pine", bg, state)
    await bg.tasks[0]()
    assert any(update[2] == "failed" and "save failed" in update[4] for update in progress.updates)

    with pytest.raises(HTTPException):
        await pine_ops.compile_pine("missing", Background(), SimpleNamespace(pine_registry=SimpleNamespace(get_source=lambda source_id: (_ for _ in ()).throw(KeyError(source_id)))))
    with pytest.raises(HTTPException):
        await pine_ops.list_artifacts("missing", SimpleNamespace(pine_registry=SimpleNamespace(get_source=lambda source_id: (_ for _ in ()).throw(KeyError(source_id)))))
    with pytest.raises(HTTPException):
        await pine_ops.inspect_artifact("pine", "missing", state)

    state.artifact_store = SimpleNamespace(_root=tmp_path / "artifacts")
    assert await pine_ops.list_artifacts("pine", state) == []
    art_dir = tmp_path / "artifacts" / "pine" / "art1"
    art_dir.mkdir(parents=True)
    (tmp_path / "artifacts" / "pine" / "not-dir").write_text("x", encoding="utf-8")
    (art_dir / "compile_meta.json").write_text(json.dumps({"compile_status": "OK", "unsafe": True}), encoding="utf-8")
    (art_dir / "generated_strategy.py").write_text("line1\nline2\n", encoding="utf-8")
    artifacts = await pine_ops.list_artifacts("pine", state)
    assert artifacts[0]["has_generated_strategy"] is True and artifacts[0]["unsafe"] is True

    state.artifact_store = SimpleNamespace(get_artifact=lambda artifact_id, source_id: {"artifact_dir": str(art_dir), "compile_meta": {"compile_status": "OK"}})
    (art_dir / "diagnostics.log").write_text("diag" * 1000, encoding="utf-8")
    inspected = await pine_ops.inspect_artifact("pine", "art1", state)
    assert inspected["generated_python_lines"] == 2
    assert len(inspected["diagnostics"]) == 2000
    assert await pine_ops.compile_progress("op") == {"operation_id": "op", "status": "running"}

    _install_pine_modules(monkeypatch, parse_ok=True)
    valid = await pine_ops.validate_pine("pine", State())
    assert valid["valid"] is True
    bad_state = State()
    bad_state.pine_registry = SimpleNamespace(get_source=lambda source_id: (_ for _ in ()).throw(KeyError(source_id)))
    with pytest.raises(HTTPException):
        await pine_ops.validate_pine("missing", bad_state)
    broken_state = State()
    broken_state.pine_registry = SimpleNamespace(get_source=lambda source_id: SimpleNamespace(source_text="x"))
    monkeypatch.setitem(sys.modules, "pine2ast", ModuleType("pine2ast"))
    assert (await pine_ops.validate_pine("pine", broken_state))["valid"] is False
