from __future__ import annotations

import logging
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

from openpine._compat import structlog as structlog_compat
from openpine.accounts.models import Account, AccountType
from openpine.adapters.bars import from_provider_bars
from openpine.batch import persistent_cache, runner as batch_runner
from openpine.config.model import OpenPineConfig
from openpine.daemon.service import DaemonService, ServiceState
from openpine.execution.binance import BinanceLiveExecutionAdapter
from openpine.execution.bybit import BybitLiveExecutionAdapter
from openpine.execution.models import (
    ExecutionUnavailableError,
    InstrumentRules,
    LiveOrderResult,
)
from openpine.execution.router import ExecutionRouter
from openpine.gateway.config import GatewayConfig
from openpine.orders.models import (
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    generate_client_order_id,
)
from openpine.streams.adapter import KlineUpdateEnvelope


def _intent(
    *,
    client_order_id: str = "client-1",
    account_id: str = "acct-1",
    order_type: OrderType = OrderType.LIMIT,
    quantity: float = 1.0,
    price: float | None = 100.0,
    stop_price: float | None = None,
) -> OrderIntent:
    return OrderIntent(
        client_order_id=client_order_id,
        strategy_id="strategy-1",
        account_id=account_id,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=order_type,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
    )


def _rules() -> InstrumentRules:
    return InstrumentRules(
        symbol="BTCUSDT",
        tick_size=0.5,
        step_size=0.1,
        min_qty=1.0,
        min_notional=10.0,
    )


class _NoCreateClient:
    async def create_order(self, **_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("invalid local orders must not reach the client")


class _RecordingCreateClient:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None

    async def create_order(self, **kwargs: Any) -> dict[str, Any]:
        self.created = kwargs
        return {"id": "ex-created", "status": "filled", "filled": "1", "average": "100"}


def test_openpine_config_path_validator_and_absolute_optional_roots(tmp_path: Path) -> None:
    assert OpenPineConfig._expand_user_paths(None) is None
    sentinel = object()
    assert OpenPineConfig._expand_user_paths(sentinel) is sentinel

    cache_root = tmp_path / "absolute-cache"
    output_root = tmp_path / "absolute-output"
    db_path = tmp_path / "absolute.sqlite"
    cfg = OpenPineConfig(
        workspace_root=tmp_path,
        data_cache_root=cache_root,
        output_root=output_root,
        db_path=db_path,
    )

    assert cfg.data_cache_root == cache_root
    assert cfg.output_root == output_root
    assert cfg.db_path == db_path


@pytest.mark.asyncio
async def test_daemon_service_default_hooks_invalid_start_and_timeout_stop() -> None:
    service = DaemonService("base")
    await service.start()
    assert service.is_running() is True
    assert "state=running" in repr(service)
    await service.stop()
    assert service.state == ServiceState.STOPPED

    invalid = DaemonService("invalid")
    invalid.state = ServiceState.STARTING
    with pytest.raises(RuntimeError, match="Cannot start service"):
        await invalid.start()

    class SlowStopService(DaemonService):
        async def _on_stop(self, timeout: float) -> None:
            await __import__("asyncio").sleep(timeout + 0.05)

    slow = SlowStopService("slow")
    slow.state = ServiceState.RUNNING
    await slow.stop(timeout=0.001)
    assert slow.state == ServiceState.STOPPED


@pytest.mark.asyncio
async def test_live_adapters_reject_local_validation_failures_and_parse_filled_without_tracking() -> None:
    rules = _rules()
    invalid_intent = _intent(quantity=0.5, price=100.0)

    binance = BinanceLiveExecutionAdapter(
        client=_NoCreateClient(), instrument_rules={"BTCUSDT": rules}
    )
    bybit = BybitLiveExecutionAdapter(
        client=_NoCreateClient(), instrument_rules={"BTCUSDT": rules}
    )

    assert bybit.client is not None
    for adapter, expected_prefix in (
        (binance, "Binance adapter validation failed"),
        (bybit, "Bybit adapter validation failed"),
    ):
        rejected = await adapter.submit_order(invalid_intent)
        assert rejected.status == OrderStatus.REJECTED
        assert rejected.error is not None and rejected.error.startswith(expected_prefix)

    parsed_binance = binance._parse_client_response(
        {"id": "binance-ex", "status": "filled", "filled": "2", "average": "50"},
        symbol=None,
    )
    parsed_bybit = bybit._parse_client_response(
        {"id": "bybit-ex", "status": "Filled", "filled": "2", "average": "50"},
        symbol=None,
    )

    assert binance._get_tracked_symbol("binance-ex") is None
    assert bybit._get_tracked_symbol("bybit-ex") is None
    assert binance._result_to_order(parsed_binance, _intent(), "local-binance", 123).status == (
        OrderStatus.FILLED
    )
    assert bybit._result_to_order(parsed_bybit, _intent(), "local-bybit", 123).status == (
        OrderStatus.FILLED
    )

    direct_filled = binance._result_to_order(
        LiveOrderResult(success=True, order_id="ex", status="filled"),
        _intent(),
        "local-direct",
        456,
    )
    assert direct_filled.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_ccxt_common_create_order_without_optional_params() -> None:
    client = _RecordingCreateClient()
    adapter = BinanceLiveExecutionAdapter(
        client=client, instrument_rules={"BTCUSDT": _rules()}
    )

    result = await adapter._call_create_order(
        _intent(client_order_id="", order_type=OrderType.LIMIT, price=100.0)
    )

    assert result.success is True
    assert client.created is not None
    assert client.created["params"] == {}
    assert client.created["type"] == "limit"
    assert client.created["price"] == "100.0"


@pytest.mark.asyncio
async def test_execution_router_reraises_adapter_unavailable_cancel() -> None:
    account = Account(
        account_id="acct-1",
        name="Live",
        provider="binance",
        exchange="binance",
        account_type=AccountType.LIVE,
        live_enabled=True,
    )

    class AccountManager:
        def get_account(self, account_id: str) -> Account | None:
            return account if account_id == account.account_id else None

    class RiskManager:
        def check_order(self, order: OrderIntent, checked_account: Account) -> tuple[bool, str | None]:
            return True, None

    class UnavailableCancelAdapter:
        async def submit_order(self, order: OrderIntent) -> Order:  # pragma: no cover
            raise AssertionError("not used")

        async def cancel_order(self, order_id: str) -> bool:
            raise ExecutionUnavailableError("exchange maintenance")

        async def get_order_status(self, order_id: str) -> Order | None:  # pragma: no cover
            return None

    router = ExecutionRouter(RiskManager(), AccountManager())
    router.register_adapter(AccountType.LIVE, UnavailableCancelAdapter())

    with pytest.raises(ExecutionUnavailableError, match="exchange maintenance"):
        await router.cancel_order("order-1", "acct-1")


def test_distribution_falls_through_unknown_command_and_main_guard(monkeypatch, tmp_path: Path) -> None:
    from openpine import distribution

    class FakeSubparsers:
        def add_parser(self, _name: str) -> "FakeSubparsers":
            return self

        def add_argument(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class FakeParser:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def add_subparsers(self, **_kwargs: Any) -> FakeSubparsers:
            return FakeSubparsers()

        def parse_args(self, _argv: list[str] | None) -> SimpleNamespace:
            return SimpleNamespace(command="unknown", root=str(tmp_path))

    with monkeypatch.context() as fake_argparse:
        fake_argparse.setattr(distribution.argparse, "ArgumentParser", FakeParser)
        assert distribution.main(["ignored"]) == 2

    monkeypatch.setattr(
        sys,
        "argv",
        [str(Path(distribution.__file__)), "manifest", "--root", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(Path(distribution.__file__)), run_name="__main__")
    assert excinfo.value.code == 0


def test_strategy_registry_none_filter_provided_hash_and_delete_without_orders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import openpine.config as config_pkg
    from openpine.registry.strategies import SQLiteStrategyRegistry

    cfg = SimpleNamespace(sqlite_path=tmp_path / "unused.sqlite", data_dir=tmp_path / "data")
    monkeypatch.setattr(config_pkg, "DEFAULT_CONFIG", cfg)

    registry = SQLiteStrategyRegistry(db_path=tmp_path / "strategies.sqlite")
    try:
        strategy = registry.create_strategy(
            name="provided-hash",
            pine_id="pine-1",
            artifact_id="artifact-1",
            params_json='{"a":1}',
            params_hash="given-hash",
            symbol="BTCUSDT",
            timeframe="1m",
        )
        assert registry.list_strategies() == [strategy]

        conn = registry._storage()
        conn.execute("CREATE TABLE backtest_trades (strategy_id TEXT)")
        conn.execute("CREATE TABLE backtest_artifacts (strategy_id TEXT)")
        conn.execute("CREATE TABLE backtest_runs (strategy_id TEXT)")
        conn.execute("CREATE TABLE orders (order_id TEXT, strategy_id TEXT)")
        conn.execute("CREATE TABLE fills (order_id TEXT)")
        conn.commit()

        registry.delete_strategy(strategy.strategy_id)
        assert registry.list_strategies() == []
    finally:
        registry.close()


def test_pine_registry_default_path_row_miss_and_name_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import openpine.pine.registry as pine_registry_mod
    from openpine.pine.registry import SQLitePineSourceRegistry

    cfg = SimpleNamespace(sqlite_path=tmp_path / "pine.sqlite")
    monkeypatch.setattr(pine_registry_mod, "DEFAULT_CONFIG", cfg)

    registry = SQLitePineSourceRegistry()
    try:
        first = registry.add_source("//@version=6\nstrategy('first')", "first")
        second = registry.add_source("//@version=6\nstrategy('second')", "second")
        assert registry.get_source(first.id) is first
        assert registry.get_source("second") is second
    finally:
        registry.close()

    class Cursor:
        def __init__(self, *, rows: list[tuple[str]] | None = None, one: tuple[Any, ...] | None = None) -> None:
            self._rows = rows or []
            self._one = one

        def fetchall(self) -> list[tuple[str]]:
            return self._rows

        def fetchone(self) -> tuple[Any, ...] | None:
            return self._one

    class FakeConnection:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Cursor:
            normalized = " ".join(sql.split()).lower()
            if normalized == "select id from pine_sources":
                return Cursor(rows=[("missing-row",)])
            if normalized.startswith("select * from pine_sources where id"):
                return Cursor(one=None)
            return Cursor()

        def commit(self) -> None:
            return None

    fake_registry = object.__new__(SQLitePineSourceRegistry)
    fake_registry._conn = FakeConnection()
    fake_registry._mem = {}
    fake_registry._init_db()
    assert fake_registry._mem == {}


def test_batch_cache_and_periodic_na_remaining_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    root = tmp_path / "root"
    root.mkdir()
    outside.write_text("x", encoding="utf-8")
    assert persistent_cache.path_fingerprint([outside], root=root)

    monkeypatch.setattr(batch_runner, "zip", lambda *_args: iter(()), raising=False)
    offset, meta = batch_runner._infer_tv_bar_index_offset_from_periodic_na(
        pd.DataFrame({"plot": [None, 1.0, None, 1.0, None]}),
        first_visible_local_index=0,
    )
    assert (offset, meta) == (0, None)


def test_small_core_singletons_and_empty_bar_coverage() -> None:
    disabled_logger = logging.getLogger("openpine.pass54.structlog")
    disabled_logger.disabled = True
    structlog_compat.BoundLoggerAdapter(disabled_logger).exception("event", detail="value")

    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=0,
        end_ms=60_000,
        source="provider",
    )
    empty_series = from_provider_bars([], query)
    assert empty_series.coverage.status == "empty"

    assert generate_client_order_id().startswith("c_")
    assert GatewayConfig.from_openpine_config(SimpleNamespace()) == GatewayConfig()
    assert "closed=True" in repr(
        KlineUpdateEnvelope(
            instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
            timeframe={"name": "1m"},
            timestamp=1,
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10.0,
            closed=True,
        )
    )
