from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.accounts.models import Account, AccountType
from openpine.execution.binance import BinanceLiveExecutionAdapter, _map_binance_status
from openpine.execution.bybit import BybitLiveExecutionAdapter, _map_bybit_status
from openpine.execution.models import ExecutionUnavailableError, InstrumentRules
from openpine.execution.paper import PaperExecutionAdapter
from openpine.execution.router import ExecutionRouter
from openpine.gateway.routes import accounts_data, dashboard, orders_positions, strategies, trading
from openpine.gateway.routes.backtest import _bar_series_fingerprint, _normalize_metrics_payload
from openpine.gateway.routes.events import list_events
from openpine.gateway.routes.optimizer import optimizer_dry_run
from openpine.gateway.routes.pine_sources import list_sources as list_pine_sources
from openpine.gateway.ws_manager import ConnectionManager
from openpine.orders.models import OrderIntent, OrderSide, OrderStatus, OrderType
from openpine.risk.manager import RiskManager


class FakeCursor:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._rows[0] if self._rows else None


class FakeStorage:
    def __init__(self):
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.db_path = Path("/tmp/openpine-test.sqlite")

    def execute(self, sql: str, params: tuple[object, ...] = ()):  # noqa: ANN201
        self.queries.append((sql, params))
        text = " ".join(sql.split()).lower()
        if "from orders" in text and "count(*)" in text and "group by" not in text:
            return FakeCursor(one=(2, 1000, 2000))
        if "from orders" in text and "group by symbol" in text:
            return FakeCursor(rows=[("BTCUSDT", 2, 2000)])
        if "left join strategy_instances" in text:
            return FakeCursor(rows=[("BTCUSDT", "s1", "MeanRev", "filled", 2, 2000)])
        if "from candle_manifests" in text and "min_open_time" in text:
            return FakeCursor(rows=[("binance", "spot", "BTCUSDT", "trade", "1m", 0, 60_000, 2, 123, "m1")])
        if "select order_id" in text:
            return FakeCursor(rows=[("o1", "s1", "a1", "c1", "BTCUSDT", "buy", "limit", 1.0, 10.0, None, None, "filled", 1.0, 10.0, None, 1, 2)])
        if "from strategy_positions" in text:
            return FakeCursor(rows=[("s1", "BTCUSDT", "long", 1.0, 10.0, 0.5, 1.0, 1, 2)])
        if "from fills" in text:
            return FakeCursor(rows=[("f1", "o1", "s1", "BTCUSDT", "buy", 1.0, 10.0, 1)])
        if "pragma table_info(events)" in text:
            return FakeCursor(rows=[(0, "timestamp_ms")])
        if "max(timestamp_ms)" in text:
            return FakeCursor(one=(1234,))
        return FakeCursor(rows=[])

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@dataclass
class FakeStrategy:
    strategy_id: str = "s1"
    id: str = "s1"
    name: str = "Strategy"
    pine_id: str = "p1"
    artifact_id: str = "a1"
    symbol: str = "BTCUSDT"
    timeframe: str = "1m"
    exchange: str = "binance"
    market_type: str = "spot"
    params_json: str = "{}"
    params_hash: str = "hash"
    mode: str = "paper"
    enabled: bool = False
    status: str = "paused"
    created_at: int = 1
    updated_at: int = 2


class FakeStrategyRegistry:
    def __init__(self):
        self.strategy = FakeStrategy()
        self.calls: list[tuple[str, object]] = []
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("CREATE TABLE strategy_instances (strategy_id text, name text, symbol text, timeframe text, exchange text, market_type text, params_json text, params_hash text, updated_at int)")
        self._conn.execute("INSERT INTO strategy_instances VALUES ('s1','Strategy','BTCUSDT','1m','binance','spot','{}','hash',1)")

    def list_strategies(self):
        return [self.strategy]

    def get_strategy(self, strategy_id: str):
        if strategy_id != self.strategy.strategy_id:
            raise KeyError(strategy_id)
        return self.strategy

    def create_strategy(self, **kwargs):
        self.strategy = FakeStrategy(**{**FakeStrategy().__dict__, **kwargs, "strategy_id": "s2"})
        return self.strategy

    def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        self.strategy.enabled = enabled
        self.calls.append(("enabled", enabled))

    def update_status(self, strategy_id: str, status: str) -> None:
        self.strategy.status = status
        self.calls.append(("status", status))

    def update_mode(self, strategy_id: str, mode: str) -> None:
        self.strategy.mode = mode
        self.calls.append(("mode", mode))

    def delete_strategy(self, strategy_id: str) -> None:
        if strategy_id != self.strategy.strategy_id:
            raise KeyError(strategy_id)
        self.calls.append(("delete", strategy_id))


class FakeState:
    def __init__(self, tmp_path: Path | None = None):
        self.storage = FakeStorage()
        self.strategy_registry = FakeStrategyRegistry()
        self.scheduler = SimpleNamespace(list_jobs=lambda: [])
        self.risk_manager = RiskManager(False)
        self._risk_kill_switch = [False]
        self.config = SimpleNamespace(data_dir=tmp_path or Path("/tmp"), sqlite_path=Path("/tmp/x.sqlite"), live_enabled=False)
        self.pine_registry = SimpleNamespace(
            list_sources=lambda: [],
            get_source=lambda source_id: SimpleNamespace(id=source_id, name="src", source_text="strategy('x')", source_type="strategy", version="1", active_artifact_id=None, created_at=1, updated_at=2),
        )
        self.artifact_store = SimpleNamespace(get_artifact=lambda artifact_id, source_id=None: {"compile_meta": {"compile_status": "OK"}})
        self.state_store = SimpleNamespace(load_snapshot=lambda strategy_id: SimpleNamespace(bar_time=123, state_data={"position": {"qty": 2, "side": "long"}}))
        self.backtest_store = SimpleNamespace(list_runs=lambda **kw: [], get_run=lambda run_id: None)
        self.backtest_cancel_requests = set()


def _intent(**kw) -> OrderIntent:
    return OrderIntent(
        client_order_id=kw.pop("client_order_id", "cid"),
        strategy_id=kw.pop("strategy_id", "s1"),
        account_id=kw.pop("account_id", "a1"),
        symbol=kw.pop("symbol", "BTCUSDT"),
        side=kw.pop("side", OrderSide.BUY),
        order_type=kw.pop("order_type", OrderType.LIMIT),
        quantity=kw.pop("quantity", 1.0),
        price=kw.pop("price", 10.0),
        stop_price=kw.pop("stop_price", None),
        **kw,
    )


def test_execution_rules_and_paper_adapter_cover_edge_paths():
    rules = InstrumentRules("BTCUSDT", tick_size=0.5, step_size=0.1, min_qty=0.5, min_notional=5, max_qty=2, market_order_supported=False)
    assert rules.validate_price(None) == (True, None)
    assert rules.validate_price(-1)[0] is False
    assert rules.validate_price(10.25)[0] is False
    assert rules.validate_price(10.5)[0] is True
    assert rules.validate_quantity(0)[0] is False
    assert rules.validate_quantity(0.15)[0] is False
    assert rules.validate_quantity(0.4)[0] is False
    assert rules.validate_quantity(3)[0] is False
    assert rules.validate_notional(0.5, 5)[0] is False
    assert rules.validate_order(1, 10, "market")[0] is False
    assert rules.validate_order(1, 10, "limit")[0] is True

    adapter = PaperExecutionAdapter()
    filled = asyncio.run(adapter.submit_order(_intent(price=None, order_type=OrderType.MARKET)))
    assert filled.status == OrderStatus.FILLED
    assert filled.avg_fill_price == 0.0
    assert asyncio.run(adapter.cancel_order("missing")) is False
    assert asyncio.run(adapter.cancel_order(filled.order_id)) is False
    assert asyncio.run(adapter.get_order_status(filled.order_id)).order_id == filled.order_id
    assert adapter.get_fills("a1") and adapter.get_orders("a1")
    assert adapter.get_fills("other") == []


class FakeExchangeClient:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.created = []

    async def create_order(self, **kwargs):
        if self.fail:
            raise RuntimeError("network down")
        self.created.append(kwargs)
        return {"id": "ex1", "status": "new", "filled": "0", "average": "10", "symbol": kwargs["symbol"], "side": kwargs["side"], "type": kwargs["type"], "amount": kwargs["amount"], "price": kwargs.get("price"), "timestamp": 123}

    async def cancel_order(self, **kwargs):
        if self.fail:
            raise RuntimeError("cancel boom")
        return {"status": "canceled"}

    async def fetch_order(self, **kwargs):
        if self.fail:
            raise RuntimeError("status boom")
        return {"id": kwargs["id"], "status": "filled", "symbol": kwargs["symbol"], "side": "buy", "type": "limit", "amount": "1", "price": "10", "filled": "1", "average": "10", "timestamp": 123}

    async def fetch_open_orders(self):
        if self.fail:
            raise RuntimeError("reconcile boom")
        return [{"id": "open1", "status": "open", "symbol": "BTCUSDT", "side": "buy", "type": "limit", "amount": "1", "price": "10", "filled": "0", "timestamp": 123}]


@pytest.mark.parametrize("adapter_cls,status_mapper", [(BinanceLiveExecutionAdapter, _map_binance_status), (BybitLiveExecutionAdapter, _map_bybit_status)])
def test_live_adapters_success_and_fail_closed_paths(adapter_cls, status_mapper):
    assert status_mapper("CANCELED") in {"cancelled", "canceled"}
    rules = {"BTCUSDT": InstrumentRules("BTCUSDT", 0.01, 0.001, 0.001, 1)}
    adapter = adapter_cls(FakeExchangeClient(), rules)
    order = asyncio.run(adapter.submit_order(_intent()))
    assert order.status == OrderStatus.NEW
    assert adapter.get_instrument_rules("BTCUSDT") is rules["BTCUSDT"]
    assert isinstance(asyncio.run(adapter.cancel_order("ex1")), bool)
    fetched = asyncio.run(adapter.get_order_status("ex1"))
    assert fetched and fetched.status == OrderStatus.FILLED
    assert asyncio.run(adapter.reconcile("a1"))[0].order_id == "open1"

    no_client = adapter_cls(None, rules)
    rejected = asyncio.run(no_client.submit_order(_intent()))
    assert rejected.status == OrderStatus.REJECTED
    with pytest.raises(ExecutionUnavailableError):
        asyncio.run(no_client.cancel_order_for_symbol("x", "BTCUSDT"))
    with pytest.raises(ExecutionUnavailableError):
        asyncio.run(no_client.get_order_status_for_symbol("x", "BTCUSDT"))
    with pytest.raises(ExecutionUnavailableError):
        asyncio.run(no_client.reconcile("a1"))

    failing = adapter_cls(FakeExchangeClient(fail=True), rules)
    failed_order = asyncio.run(failing.submit_order(_intent()))
    assert failed_order.status == OrderStatus.REJECTED
    with pytest.raises(ExecutionUnavailableError):
        asyncio.run(failing.cancel_order_for_symbol("x", "BTCUSDT"))
    with pytest.raises(ExecutionUnavailableError):
        asyncio.run(failing.get_order_status_for_symbol("x", "BTCUSDT"))
    with pytest.raises(ExecutionUnavailableError):
        asyncio.run(failing.reconcile("a1"))


def test_execution_router_risk_and_adapter_boundaries():
    class Accounts:
        def __init__(self, account=None):
            self.account = account
        def get_account(self, account_id):
            return self.account
    class Adapter:
        async def submit_order(self, order):
            return await PaperExecutionAdapter().submit_order(order)
        async def cancel_order(self, order_id):
            return True
    account = Account(account_id="a1", name="paper", provider="local", exchange="binance", mode=AccountType.PAPER)
    router = ExecutionRouter(RiskManager(False), Accounts(account))
    router.register_adapter(AccountType.PAPER, Adapter())
    assert asyncio.run(router.submit_order(_intent())).status == OrderStatus.FILLED
    assert asyncio.run(router.cancel_order("o1", "a1")) is True
    missing = ExecutionRouter(RiskManager(False), Accounts(None))
    assert asyncio.run(missing.submit_order(_intent())).status == OrderStatus.REJECTED
    no_adapter = ExecutionRouter(RiskManager(False), Accounts(account))
    assert asyncio.run(no_adapter.submit_order(_intent())).status == OrderStatus.REJECTED


def test_accounts_data_helpers_cover_inventory_and_delete_paths(tmp_path, monkeypatch):
    state = FakeState(tmp_path)
    groups: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    key = ("binance", "spot", "BTCUSDT", "trade", "1m")
    entry = accounts_data._series_entry(groups, key)
    accounts_data._extend_series(entry, 2, 0, 60_000, 10, "unit", "u1")
    accounts_data._extend_series(entry, 2, 60_000, 120_000, 10, "derived", "u2")
    assert accounts_data._series_role(entry) == "source"
    assert accounts_data._estimate_bars_for_window(0, 120_000, "1m") == 2
    assert accounts_data._estimate_bars_for_window(10, 0, "1m") == 0
    coalesced = accounts_data._coalesce_ranges(entry["ranges"], "1m")
    assert len(coalesced) == 1
    compact = accounts_data._compact_ranges([{"from_ms": i, "to_ms": i, "rows": 1} for i in range(10)])
    assert compact[3]["collapsed"] == 5
    assert accounts_data._estimate_unique_bars([{"from_ms": None, "to_ms": None, "rows": 7}], "1m") == 7
    assert accounts_data._freshness_status(None, "1m") == "empty"
    assert accounts_data._series_id(key)
    assert accounts_data._orders_summary(state)["total"] == 2

    root = tmp_path / "segments"
    state.config.data_dir = tmp_path
    monkeypatch.setattr(accounts_data, "_marketdata_store_root", lambda _state: root)
    index = root / "index.sqlite"
    index.parent.mkdir(parents=True)
    with sqlite3.connect(index) as db:
        db.execute("CREATE TABLE marketdata_segments (id text, exchange text, market text, symbol text, timeframe text, start_time int, end_time int, rows_count int, source_kind text)")
        db.execute("INSERT INTO marketdata_segments VALUES ('seg1','binance','spot','BTCUSDT','1m',0,60000,2,'trade_kline')")
    segdir = accounts_data._marketdata_segment_dir(root, "binance", "spot", "BTCUSDT", "1m", "trade_kline")
    segdir.mkdir(parents=True)
    (segdir / "part.parquet").write_text("x")
    groups = {}
    accounts_data._merge_marketdata_segment_groups(state, groups)
    assert list(groups.values())[0]["bar_count"] == 2
    assert accounts_data._delete_marketdata_segment_series(state, {"exchange":"binance","market_type":"spot","symbol":"BTCUSDT","timeframe":"1m","source_kinds":["trade_kline"]}) >= 1


def test_gateway_simple_routes_and_dashboard_helpers():
    state = FakeState()
    listed = asyncio.run(strategies.list_strategies(state.strategy_registry))
    assert listed[0].strategy_id == "s1"
    assert asyncio.run(strategies.get_strategy("s1", state.strategy_registry)).symbol == "BTCUSDT"
    with pytest.raises(HTTPException):
        asyncio.run(strategies.get_strategy("missing", state.strategy_registry))
    assert asyncio.run(strategies.strategy_action("s1", state, action="start"))["status"] == "ok"
    assert asyncio.run(trading.start_paper(SimpleNamespace(strategy_id="s1"), state)).status == "running"
    assert asyncio.run(trading.stop_paper(SimpleNamespace(strategy_id="s1"), state))["status"] == "stopped"
    assert asyncio.run(trading.get_trading_status("s1", state)).position_side == "long"
    assert dashboard._count_jobs([{"status": "done"}, {"status": "running"}], "done") == 1
    assert dashboard._normalize_job_status("completed") == "done"
    assert dashboard._strategy_health(state, state.strategy_registry.strategy)["status"] in {"ok", "warning", "error"}


def test_orders_positions_routes_with_fake_storage():
    state = FakeState()
    orders = asyncio.run(orders_positions.list_orders(strategy_id="s1", status="filled", state=state))
    assert orders[0]["order_id"] == "o1"
    assert asyncio.run(orders_positions.get_order("o1", state))["symbol"] == "BTCUSDT"
    assert asyncio.run(orders_positions.list_positions("s1", state))[0]["symbol"] == "BTCUSDT"
    positions = asyncio.run(orders_positions.get_strategy_positions("s1", state))
    assert positions["strategy_id"] == "s1"


def test_backtest_and_misc_route_helpers():
    class Q:
        instrument = SimpleNamespace(exchange="binance", market="spot", symbol="BTCUSDT")
        timeframe = SimpleNamespace(canonical="1m")
        start_ms = 0
        end_ms = 120_000
    series = SimpleNamespace(query=Q(), bars=[SimpleNamespace(time=0, time_close=60_000, open=1, high=2, low=0.5, close=1.5, volume=10), SimpleNamespace(time=60_000, time_close=120_000, open=1.5, high=2, low=1, close=1.8, volume=None)])
    assert _bar_series_fingerprint(series) == _bar_series_fingerprint(series)
    assert _normalize_metrics_payload({"a": 1}) == {"a": 1}
    assert _normalize_metrics_payload(None) is None
    assert asyncio.run(list_events(limit=3, state=FakeState())) == []
    state = FakeState()
    resp = asyncio.run(optimizer_dry_run(SimpleNamespace(strategy_id="s1", trials=2), state))
    assert resp.strategy_id == "s1"
    assert asyncio.run(list_pine_sources(SimpleNamespace(list_sources=lambda: []))) == []


class FakeWs:
    def __init__(self, fail_send: bool = False):
        self.accepted = False
        self.sent = []
        self.fail_send = fail_send
    async def accept(self):
        self.accepted = True
    async def send_json(self, payload):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(payload)

    async def send_text(self, payload):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(payload)


def test_ws_manager_full_lifecycle():
    manager = ConnectionManager()
    ws = FakeWs()
    asyncio.run(manager.connect(ws, client_id="c1"))
    assert ws.accepted
    asyncio.run(manager.send_personal("c1", {"hello": "world"}))
    asyncio.run(manager.broadcast({"event": "x"}))
    manager.update_progress("job1", "compile", "running", pct=50)
    assert manager.get_progress("job1")["pct"] == 50
    asyncio.run(manager.disconnect("c1"))
    failing = FakeWs(fail_send=True)
    asyncio.run(manager.connect(failing, client_id="c2"))
    asyncio.run(manager.send_personal("c2", {"boom": True}))
    assert manager.active_count == 0

class FakeBacktestStore:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.run = SimpleNamespace(
            run_id="run1",
            strategy_id="s1",
            status="running",
            started_at=1,
            finished_at=None,
            symbol="BTCUSDT",
            timeframe="1m",
            from_time=0,
            to_time=60_000,
            bars_processed=10,
        )
        self.report = tmp_path / "report.md"
        self.report.write_text("# Report", encoding="utf-8")
        self.artifacts = [
            SimpleNamespace(artifact_type="report_md", path=str(self.report)),
            SimpleNamespace(artifact_type="equity_curve", path=str(tmp_path / "missing.parquet")),
        ]
        self.trades = [
            SimpleNamespace(
                trade_id="t1",
                entry_time=1,
                exit_time=2,
                direction="long",
                entry_price=10.0,
                exit_price=11.0,
                qty=1.0,
                net_pnl=1.0,
            )
        ]

    def create_run(self, request):
        return "run-new"

    def list_runs(self, strategy_id, limit=50):
        return [self.run]

    def list_all_runs(self, limit=50):
        return [self.run]

    def get_run(self, run_id):
        return self.run if run_id == "run1" else None

    def delete_run(self, run_id):
        return run_id == "run1"

    def list_trades(self, run_id):
        return list(self.trades)

    def get_metrics(self, run_id):
        return {"net_profit": 1.0}

    def list_artifacts(self, run_id):
        return list(self.artifacts)


def test_backtest_routes_list_get_action_export_and_artifacts(tmp_path):
    from openpine.gateway.routes import backtest

    state = FakeState(tmp_path)
    state.backtest_store = FakeBacktestStore(tmp_path)
    details = asyncio.run(backtest.list_runs(state=state))
    assert details[0].run_id == "run1"
    assert asyncio.run(backtest.get_run("run1", state)).version == 1
    with pytest.raises(HTTPException):
        asyncio.run(backtest.get_run("missing", state))
    assert asyncio.run(backtest.run_action("run1", "cancel", state))["accepted"] is True
    with pytest.raises(HTTPException):
        asyncio.run(backtest.run_action("run1", "pause", state))
    assert asyncio.run(backtest.get_run_trades("run1", state))[0].trade_id == "t1"
    assert asyncio.run(backtest.get_run_report("run1", state))["data"] == "# Report"
    exported = asyncio.run(backtest.export_run("run1", state))
    assert exported["metrics"]["net_profit"] == 1.0
    assert exported["trades"][0]["trade_id"] == "t1"
    assert asyncio.run(backtest.delete_run("run1", state)) is None
    with pytest.raises(HTTPException):
        asyncio.run(backtest.delete_run("missing", state))

    backtest.ws_manager.update_progress(
        "r-progress", "backtest", "running", 33.0, "Bars: 1,000/2,000"
    )
    progress = asyncio.run(backtest.get_progress("r-progress"))
    assert progress.bars_processed == 1000
    backtest.ws_manager.update_progress(
        "r-detail",
        "backtest",
        "running",
        50.0,
        "half",
        detail={"bars_processed": 5, "total_bars": 10},
    )
    assert asyncio.run(backtest.get_progress("r-detail")).total_bars == 10
    assert asyncio.run(backtest.get_progress("none")) is None
    assert asyncio.run(backtest.get_progress_detail("r-detail"))["status"] == "running"

    with pytest.raises(HTTPException):
        asyncio.run(backtest.get_run_equity("missing", state))
    with pytest.raises(HTTPException):
        asyncio.run(backtest.get_run_equity("run1", state))
    with pytest.raises(HTTPException):
        asyncio.run(backtest.get_run_plots("run1", state))
    with pytest.raises(HTTPException):
        asyncio.run(backtest.get_run_bar_outputs("run1", state))


def test_gateway_trading_and_strategy_error_paths():
    state = FakeState()
    state.strategy_registry.strategy.status = "error"
    with pytest.raises(HTTPException):
        asyncio.run(trading.start_paper(SimpleNamespace(strategy_id="s1"), state))
    with pytest.raises(HTTPException):
        asyncio.run(strategies.strategy_action("s1", state, action="start"))
    assert asyncio.run(strategies.strategy_action("s1", state, action="clear_error"))["status"] == "ok"
    with pytest.raises(HTTPException):
        asyncio.run(strategies.strategy_action("s1", state, action="unknown"))
    with pytest.raises(HTTPException):
        asyncio.run(trading.start_live(SimpleNamespace(strategy_id="missing"), state))
    with pytest.raises(HTTPException):
        asyncio.run(trading.stop_live(SimpleNamespace(strategy_id="missing"), state))


def test_data_inventory_summary_and_risk_routes(tmp_path, monkeypatch):
    state = FakeState(tmp_path)
    monkeypatch.setattr(accounts_data, "_persistent_cache_size_bytes", lambda: 5)
    monkeypatch.setattr(accounts_data, "_database_size_bytes", lambda _state: 10)
    monkeypatch.setattr(accounts_data, "_candle_store_size_bytes", lambda _state: 15)
    monkeypatch.setattr(
        accounts_data, "_data_series_inventory", lambda _state: [{"bar_count": 3}]
    )
    summary = accounts_data._data_summary(state)
    assert summary["total_size_bytes"] == 30
    status = asyncio.run(accounts_data.risk_status(state))
    assert status.kill_switch is False
    toggled = asyncio.run(accounts_data.toggle_kill_switch(SimpleNamespace(enabled=True), state))
    assert toggled["kill_switch"] is True

class FakePineRegistry:
    def __init__(self):
        self.sources = {
            "p1": SimpleNamespace(
                id="p1",
                name="source",
                source_type="strategy",
                version="1",
                source_text="strategy('x')",
                source_hash="h",
                active_artifact_id=None,
                created_at=1,
                updated_at=2,
            )
        }
        self._mem = self.sources
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
            "CREATE TABLE pine_sources (id text, name text, source_text text, source_hash text, source_type text, updated_at int)"
        )
        self._conn.execute("INSERT INTO pine_sources VALUES (?,?,?,?,?,?)", ("p1", "source", "strategy(\'x\')", "h", "strategy", 2))

    def list_sources(self):
        return list(self.sources.values())

    def get_source(self, source_id):
        if source_id in self.sources:
            return self.sources[source_id]
        for src in self.sources.values():
            if src.name == source_id:
                return src
        raise KeyError(source_id)

    def add_source(self, source_text, name):
        src = SimpleNamespace(
            id="p2",
            name=name,
            source_type="strategy",
            version="1",
            source_text=source_text,
            source_hash="new",
            active_artifact_id=None,
            created_at=3,
            updated_at=4,
        )
        self.sources[src.id] = src
        return src

    def delete_source(self, source_id):
        if source_id not in self.sources:
            raise KeyError(source_id)
        self.sources.pop(source_id)

    def set_active_artifact(self, source_id, artifact_id):
        self.sources[source_id].active_artifact_id = artifact_id


class FakeArtifactStore:
    def __init__(self, root: Path):
        self._root = root
        art_dir = root / "p1" / "art1"
        art_dir.mkdir(parents=True)
        (art_dir / "compile_meta.json").write_text(json.dumps({"compile_status": "OK"}), encoding="utf-8")
        (art_dir / "generated_strategy.py").write_text("class GeneratedStrategy: pass\n", encoding="utf-8")
        (art_dir / "diagnostics.log").write_text("all good", encoding="utf-8")

    def get_artifact(self, artifact_id, source_id=None):
        art_dir = self._root / (source_id or "p1") / artifact_id
        if not art_dir.exists():
            raise FileNotFoundError(artifact_id)
        return {"artifact_dir": str(art_dir), "compile_meta": {"compile_status": "OK"}}

    def save_artifact(self, **kwargs):
        return None


def test_gateway_server_factory_and_env_flags(monkeypatch):
    from openpine.gateway.config import GatewayConfig
    from openpine.gateway.server import _env_flag, create_app

    monkeypatch.delenv("OPENPINE_TEST_FLAG", raising=False)
    assert _env_flag("OPENPINE_TEST_FLAG", True) is True
    monkeypatch.setenv("OPENPINE_TEST_FLAG", "yes")
    assert _env_flag("OPENPINE_TEST_FLAG") is True
    monkeypatch.setenv("OPENPINE_TEST_FLAG", "0")
    assert _env_flag("OPENPINE_TEST_FLAG", True) is False
    app = create_app(GatewayConfig(api_prefix="/api", cors_origins=["*"]))
    paths = {route.path for route in app.routes}
    assert "/health" in paths and "/" in paths
    assert any(path.startswith("/api") for path in paths)


def test_pine_source_routes_and_pine_ops_artifacts(tmp_path, monkeypatch):
    from openpine.gateway.routes import pine_ops, pine_sources
    from openpine.gateway.schemas import PineSourceCreate, PineSourceUpdate

    registry = FakePineRegistry()
    state = FakeState(tmp_path)
    state.pine_registry = registry
    state.artifact_store = FakeArtifactStore(tmp_path / "artifacts")

    assert asyncio.run(pine_sources.list_sources(registry))[0].id == "p1"
    created = asyncio.run(
        pine_sources.create_source(PineSourceCreate(name="new", source_text="indicator('x')"), registry)
    )
    assert created.id == "p2"
    assert asyncio.run(pine_sources.get_source("p1", registry)).name == "source"
    updated = asyncio.run(
        pine_sources.update_source(
            "p1",
            PineSourceUpdate(name="renamed", source_text="strategy('y')", source_type="strategy"),
            registry,
        )
    )
    assert updated.name == "renamed"
    with pytest.raises(HTTPException):
        asyncio.run(pine_sources.get_source("missing", registry))
    preview = asyncio.run(pine_sources.delete_source_preview("p1", state))
    assert preview["source_id"] == "p1"
    artifacts = asyncio.run(pine_ops.list_artifacts("p1", state))
    assert artifacts[0]["artifact_id"] == "art1"
    inspected = asyncio.run(pine_ops.inspect_artifact("p1", "art1", state))
    assert inspected["generated_python_lines"] == 1
    with pytest.raises(HTTPException):
        asyncio.run(pine_ops.list_artifacts("missing", state))
    with pytest.raises(HTTPException):
        asyncio.run(pine_ops.inspect_artifact("p1", "missing", state))
    pine_ops.ws_manager.update_progress("compile-x", "compile", "queued")
    assert asyncio.run(pine_ops.compile_progress("compile-x"))["status"] == "queued"

    class BadParser:
        @staticmethod
        def parse_code(*_args, **_kwargs):
            raise RuntimeError("parse unavailable")
    result = asyncio.run(pine_ops.validate_pine("p1", state))
    assert "source_id" in result


def test_strategy_crud_compare_and_dashboard_full_paths(tmp_path):
    from openpine.gateway.schemas import CompareTvRequest, StrategyCreate, StrategyMode, StrategyUpdate

    state = FakeState(tmp_path)
    state.pine_registry = FakePineRegistry()
    state.artifact_store = FakeArtifactStore(tmp_path / "artifacts")
    created = asyncio.run(
        strategies.create_strategy(
            StrategyCreate(
                name="created",
                pine_id="p1",
                artifact_id="art1",
                symbol="ETHUSDT",
                timeframe="5m",
                exchange="binance",
                market_type="spot",
                params_json='{"len": 14}',
                mode=StrategyMode.PAPER,
            ),
            state,
        )
    )
    assert created.strategy_id == "s2"
    updated = asyncio.run(
        strategies.update_strategy(
            "s2",
            StrategyUpdate(
                name="updated",
                symbol="SOLUSDT",
                timeframe="15m",
                enabled=True,
                mode=StrategyMode.LIVE,
                params_json='{"len": 21}',
            ),
            state,
        )
    )
    assert updated.strategy_id == "s2"
    assert any(call == ("enabled", True) for call in state.strategy_registry.calls)
    assert asyncio.run(strategies.strategy_enable("s2", state.strategy_registry))["status"] == "ok"
    assert asyncio.run(strategies.strategy_disable("s2", state.strategy_registry))["status"] == "ok"
    preview = asyncio.run(strategies.delete_strategy_preview("s2", state))
    assert preview["resources"]["strategy_rows"] == 1
    asyncio.run(strategies.delete_strategy("s2", state.strategy_registry))
    with pytest.raises(HTTPException):
        asyncio.run(
            strategies.create_strategy(
                StrategyCreate(name="bad", pine_id="missing", artifact_id="art1", symbol="BTCUSDT", timeframe="1m"),
                state,
            )
        )

    op_csv = tmp_path / "op.csv"
    tv_csv = tmp_path / "tv.csv"
    op_csv.write_text("time,plot,close\n1000,1.0,2.0\n2000,2.0,3.0\n", encoding="utf-8")
    tv_csv.write_text("bar_time,plot,close\n1000,1.0,2.1\n2000,2.5,3.0\n", encoding="utf-8")
    cmp_result = asyncio.run(
        strategies.strategy_compare_tv(
            "s1",
            CompareTvRequest(openpine_plots_path=str(op_csv), tv_chart_path=str(tv_csv), abs_tol=0.1),
            state,
        )
    )
    assert cmp_result["status"] == "mismatch"
    no_common = tmp_path / "tv2.csv"
    no_common.write_text("time,plot\n3000,1\n", encoding="utf-8")
    assert asyncio.run(
        strategies.strategy_compare_tv(
            "s1",
            CompareTvRequest(openpine_plots_path=str(op_csv), tv_chart_path=str(no_common)),
            state,
        )
    )["status"] == "error"
    with pytest.raises(HTTPException):
        asyncio.run(
            strategies.strategy_compare_tv(
                "s1",
                CompareTvRequest(openpine_plots_path=str(tmp_path / "missing.csv"), tv_chart_path=str(tv_csv)),
                state,
            )
        )

    state._startup_time = 0
    state._risk_kill_switch = [False]
    state._fetcher = SimpleNamespace(last_fetch_at=999)
    dash = asyncio.run(dashboard.dashboard(state))
    assert dash.strategies
    assert dash.last_event_time == 1234
    assert dash.last_bar_update == 999


def test_events_websocket_and_old_schema_event_listing():
    from openpine.gateway.routes import events

    class OldEventStorage(FakeStorage):
        def execute(self, sql: str, params: tuple[object, ...] = ()):  # noqa: ANN201
            text = " ".join(sql.split()).lower()
            if "pragma table_info(events)" in text:
                return FakeCursor(rows=[(0, "payload_json"), (1, "created_at")])
            if "from events" in text:
                return FakeCursor(rows=[("e1", "order.filled", json.dumps({"strategy_id": "s1"}), 111)])
            return super().execute(sql, params)

    state = FakeState()
    state.storage = OldEventStorage()
    rows = asyncio.run(events.list_events(event_type="order.filled", strategy_id="s1", state=state))
    assert rows[0].payload["strategy_id"] == "s1"

    class WsDisconnects(FakeWs):
        async def receive_text(self):
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()

    class WsRaises(FakeWs):
        async def receive_text(self):
            raise RuntimeError("broken")

    ws1 = WsDisconnects()
    asyncio.run(events.websocket_events(ws1))
    assert ws1.accepted
    ws2 = WsRaises()
    asyncio.run(events.websocket_events(ws2))
    assert ws2.accepted


def test_additional_strategy_and_trading_edge_paths(tmp_path):
    from openpine.gateway.schemas import LiveStartRequest, StrategyCreate, StrategyUpdate

    state = FakeState(tmp_path)
    state.config.live_enabled = True
    live = asyncio.run(trading.start_live(LiveStartRequest(strategy_id="s1"), state))
    assert live.mode == "live"
    assert asyncio.run(trading.stop_live(LiveStartRequest(strategy_id="s1"), state))["status"] == "stopped"
    with pytest.raises(HTTPException):
        asyncio.run(trading.get_trading_status("missing", state))

    state.strategy_registry.strategy.status = "paused"
    with pytest.raises(HTTPException):
        asyncio.run(strategies.strategy_action("s1", state, action="clear_error"))
    untouched = asyncio.run(strategies.update_strategy("s1", StrategyUpdate(), state))
    assert untouched.strategy_id == "s1"

    class MissingArtifactStore:
        def get_artifact(self, artifact_id, source_id=None):
            raise FileNotFoundError(artifact_id)

    state.pine_registry = FakePineRegistry()
    state.artifact_store = MissingArtifactStore()
    with pytest.raises(HTTPException):
        asyncio.run(
            strategies.create_strategy(
                StrategyCreate(name="bad-art", pine_id="p1", artifact_id="missing", symbol="BTCUSDT", timeframe="1m"),
                state,
            )
        )

    class BadCompileStore:
        def get_artifact(self, artifact_id, source_id=None):
            return {"compile_meta": {"compile_status": "ERROR"}}

    state.artifact_store = BadCompileStore()
    with pytest.raises(HTTPException):
        asyncio.run(
            strategies.create_strategy(
                StrategyCreate(name="bad-compile", pine_id="p1", artifact_id="art1", symbol="BTCUSDT", timeframe="1m"),
                state,
            )
        )
