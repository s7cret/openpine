from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openpine._compat import structlog as structlog_compat
from openpine.accounts import Account, AccountManager, AccountType
from openpine.config.env import load_env_file
from openpine.orders.models import OrderIntent, OrderSide, OrderType
from openpine.risk.manager import (
    MaxOrdersPerMinuteRule,
    MaxPositionSizeRule,
    RiskManager,
)
from openpine.storage.backends import StorageBackendRegistry
from openpine.storage.manifests import ManifestStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage
from openpine.telegram_commands import (
    TelegramButtonSpec,
    TelegramCommandError,
    catalog_families,
    confirm_delete_keyboard,
    generate_help_text,
    home_menu_keyboard,
    inline_keyboard,
    map_callback_data,
    map_telegram_command,
    pine_list_keyboard,
    reports_keyboard,
    risk_keyboard,
    strategy_actions_keyboard,
    strategy_list_keyboard,
)


def test_structlog_compat_fallback_exposes_structlog_like_logger(monkeypatch):
    monkeypatch.setattr(structlog_compat, "_structlog", None)
    logger = structlog_compat.get_logger("test")
    assert logger.bind(component="x").new().unbind("component") is logger
    logger.debug("debug", answer=42)
    logger.info("info")
    logger.warning("warn")
    logger.error("error", detail="x")


def test_manifest_store_roundtrip_and_missing(tmp_path: Path):
    store = ManifestStore(tmp_path)
    assert store.get_manifest("missing") is None
    store.save_manifest("s1", {"strategy_id": "s1", "enabled": True})
    store.save_manifest("s2", {"strategy_id": "s2"})
    assert store.get_manifest("s1") == {"strategy_id": "s1", "enabled": True}
    assert set(store.list_manifests()) == {"s1", "s2"}


def test_storage_backend_registry_reports_known_backends():
    registry = StorageBackendRegistry()
    names = {backend.name for backend in registry.list_backends()}
    assert {"sqlite", "parquet", "duckdb", "postgres"}.issubset(names)
    assert registry.get_by_name("sqlite") is not None
    assert registry.get_by_role(
        __import__(
            "openpine.storage.adapters", fromlist=["BackendRole"]
        ).BackendRole.CONTROL
    )
    assert registry.get_by_name("missing") is None
    rows = registry.summary_table()
    assert {row["name"] for row in rows}.issuperset({"sqlite", "parquet"})


def test_account_manager_full_crud(tmp_path: Path):
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    try:
        MigrationRunner().run_migrations(storage)
        manager = AccountManager(storage)
        account = manager.create_account(
            name="paper-binance",
            exchange="binance",
            provider="ccxt",
            market_type="spot",
            mode=AccountType.PAPER,
            permissions="trade:paper",
            config={"recv_window": 5000},
        )
        assert account.live_enabled is False
        assert manager.get_account(account.account_id).name == "paper-binance"
        assert (
            manager.list_accounts(AccountType.PAPER)[0].account_id == account.account_id
        )
        assert (
            manager.list_accounts_by_provider("ccxt", "binance")[0].account_id
            == account.account_id
        )
        manager.set_live_enabled(account.account_id, True)
        assert manager.get_account(account.account_id).live_enabled is True
        manager.delete_account(account.account_id)
        assert manager.get_account(account.account_id) is None
    finally:
        storage.close()


def test_risk_manager_rules_and_violations(monkeypatch):
    account = Account(
        account_id="acct_1",
        name="acct",
        exchange="binance",
        provider="ccxt",
        market_type="spot",
    )
    order = OrderIntent(
        client_order_id="c1",
        strategy_id="s1",
        account_id="acct_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=2.0,
        price=100.0,
    )
    manager = RiskManager()
    manager.add_rule(MaxPositionSizeRule(max_notional=1000.0))
    assert manager.check_order(order, account) == (True, None)
    manager.set_kill_switch(True)
    allowed, reason = manager.check_order(order, account)
    assert allowed is False and "Kill switch" in reason
    assert manager.get_violations(account.account_id)
    manager.clear_violations(account.account_id)
    assert manager.get_violations(account.account_id) == []

    manager = RiskManager()
    manager.add_rule(MaxPositionSizeRule(max_notional=50.0))
    allowed, reason = manager.check_order(order, account)
    assert allowed is False and "exceeds" in reason

    ticks = iter([1000, 1001, 1002])
    monkeypatch.setattr("openpine.risk.manager.time.time", lambda: next(ticks))
    per_minute = MaxOrdersPerMinuteRule(max_orders=1)
    assert per_minute.check(order, account) == (True, None)
    allowed, reason = per_minute.check(order, account)
    assert allowed is False and "per minute" in reason


def test_telegram_command_catalog_maps_commands_and_callbacks():
    families = catalog_families()
    assert "strategy" in families and "data" in families
    assert map_telegram_command("/version") == ["version"]
    assert map_telegram_command("/op strategy list --json") == [
        "strategy",
        "list",
        "--json",
    ]
    assert map_telegram_command("/strategy_status strat_1") == [
        "strategy",
        "status",
        "strat_1",
    ]
    assert "strategy:" in generate_help_text("strategy")
    with pytest.raises(TelegramCommandError):
        map_telegram_command("/does_not_exist")
    with pytest.raises(TelegramCommandError):
        map_telegram_command("")

    assert map_callback_data("op:status") == ["doctor"]
    assert map_callback_data("op:strategy:pause:s1") == ["strategy", "pause", "s1"]
    assert map_callback_data("op:pine:show:foo") == ["pine", "show", "foo"]
    with pytest.raises(TelegramCommandError):
        map_callback_data("bad:callback")

    keyboards = [
        inline_keyboard(((TelegramButtonSpec("A", "op:home"),),)),
        home_menu_keyboard(),
        confirm_delete_keyboard("s1"),
        strategy_list_keyboard([{"strategy_id": "s1", "symbol": "BTCUSDT"}]),
        pine_list_keyboard([{"name": "alpha"}]),
        strategy_actions_keyboard("s1"),
        risk_keyboard(),
        reports_keyboard(),
    ]
    assert all("inline_keyboard" in keyboard for keyboard in keyboards)


def test_load_env_file_sets_missing_keys_only(tmp_path: Path, monkeypatch):
    env_path = tmp_path / "env"
    env_path.write_text(
        "# comment\nOPENPINE_A=one\ninvalid-line\nOPENPINE_B = two \n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENPINE_A", "existing")
    monkeypatch.delenv("OPENPINE_B", raising=False)
    load_env_file(env_path)
    assert sys.modules["os"].environ["OPENPINE_A"] == "existing"
    assert sys.modules["os"].environ["OPENPINE_B"] == "two"
    load_env_file(tmp_path / "missing")


def test_python_module_entrypoint_invokes_cli(monkeypatch):
    calls: list[bool] = []

    def fake_main() -> None:
        calls.append(True)

    import importlib

    cli_main_module = importlib.import_module("openpine.cli.main")
    monkeypatch.setattr(cli_main_module, "main", fake_main)
    sys.modules.pop("openpine.__main__", None)
    import runpy

    runpy.run_module("openpine", run_name="__main__")
    assert calls == [True]


def test_sqlite_storage_finalizer_is_idempotent(tmp_path):
    from openpine.storage.sqlite_storage import SQLiteStorage

    storage = SQLiteStorage(tmp_path / "finalizer.sqlite")
    storage.execute("CREATE TABLE IF NOT EXISTS t(id INTEGER)")
    storage.close()
    storage.__del__()
    assert storage._conn is None
