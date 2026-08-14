from __future__ import annotations

import hashlib
import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.state.store import StateStore, StrategyState
from openpine.storage import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage


def _strategy_state(bar_time: int, runtime_state: object) -> StrategyState:
    return StrategyState(
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        timeframe={"canonical": "1m"},
        state_data=runtime_state,  # type: ignore[arg-type]
        bar_time=bar_time,
        saved_at=bar_time,
    )


def test_state_store_roundtrips_backtest_resume_state_without_pickle(tmp_path: Path) -> None:
    from backtest_engine import BacktestResumeState, Position
    from backtest_engine.core.state_snapshot import BrokerSnapshot

    broker = BrokerSnapshot(
        cash=1000.0,
        equity=1001.0,
        peak_equity=1002.0,
        max_drawdown=1.0,
        max_drawdown_percent=0.1,
        trough_equity=999.0,
        max_runup=2.0,
        max_runup_percent=0.2,
        position=Position(size=2.0, avg_price=10.0, direction="long"),
        orders=[],
        fills=[],
        closed_trades=[],
        open_trades=[],
        last_trade_bar=4,
    )
    resume = BacktestResumeState(
        bar_index=4,
        config_snapshot_hash="cfg",
        strategy_state={"seen": {1, 2}},
        runtime_state={"buffer": (1.0, 2.0)},
        broker_state=broker,
        metadata={"resume_contract": "engine-broker-snapshot-v1"},
    )
    store = StateStore(tmp_path)

    store.save_snapshot(_strategy_state(300_000, resume))
    loaded = store.load_runtime_snapshot("strategy-1")

    assert isinstance(loaded, BacktestResumeState)
    assert loaded.bar_index == 4
    assert isinstance(loaded.broker_state, BrokerSnapshot)
    assert loaded.broker_state.position.direction == "long"
    assert loaded.strategy_state == {"seen": {1, 2}}
    assert loaded.runtime_state == {"buffer": (1.0, 2.0)}


def test_trading_status_reads_bar_and_position_from_typed_resume_snapshot(tmp_path: Path) -> None:
    from backtest_engine import BacktestResumeState, Position
    from backtest_engine.core.state_snapshot import BrokerSnapshot
    from openpine.gateway.routes.trading import get_trading_status

    resume = BacktestResumeState(
        bar_index=7,
        config_snapshot_hash="cfg",
        broker_state=BrokerSnapshot(
            cash=1000.0,
            equity=1001.0,
            peak_equity=1002.0,
            max_drawdown=1.0,
            max_drawdown_percent=0.1,
            trough_equity=999.0,
            max_runup=2.0,
            max_runup_percent=0.2,
            position=Position(size=-3.0, avg_price=10.0, direction="short"),
        ),
    )
    store = StateStore(tmp_path)
    store.save_snapshot(_strategy_state(480_000, resume))
    strategy = SimpleNamespace(mode="paper", status="running")
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        state_store=store,
    )

    status = asyncio.run(get_trading_status("strategy-1", state))

    assert status.last_bar_time == 480_000
    assert status.position_qty == -3.0
    assert status.position_side == "short"


def test_state_store_verifies_snapshot_checksum_from_metadata(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    meta = store.save_snapshot(_strategy_state(60_000, {"value": 1}))
    assert meta is not None
    assert meta.checksum
    snapshot = next((tmp_path / "strategy_id=strategy-1").glob("*.state.msgpack.zst"))
    original = snapshot.read_bytes()
    assert meta.checksum == hashlib.sha256(original).hexdigest()
    snapshot.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))

    with pytest.raises(Exception, match="Checksum mismatch"):
        store.load_snapshot("strategy-1")


def test_state_store_observes_snapshots_written_by_another_process_instance(tmp_path: Path) -> None:
    reader = StateStore(tmp_path)
    writer = StateStore(tmp_path)
    assert reader.latest_snapshot_metadata("strategy-1") is None

    writer.save_snapshot(_strategy_state(60_000, {"value": 1}))

    latest = reader.latest_snapshot_metadata("strategy-1")
    assert latest is not None
    assert latest.bar_time == 60_000


def test_state_store_prunes_superseded_snapshots_to_retention_limit(tmp_path: Path) -> None:
    store = StateStore(tmp_path, max_snapshots_per_strategy=2)
    for index in range(5):
        store.save_snapshot(_strategy_state(index * 60_000, {"value": index}))

    snapshots = store.list_snapshots("strategy-1")
    files = list((tmp_path / "strategy_id=strategy-1").glob("*.state.msgpack.zst"))
    assert len(snapshots) == 2
    assert len(files) == 2
    assert [snapshot.bar_time for snapshot in snapshots] == [240_000, 180_000]


def test_orders_updated_cursor_has_covering_strategy_index(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "migrations.sqlite")
    MigrationRunner().run_migrations(storage)
    indexes = {
        row[1]
        for row in storage.execute("PRAGMA index_list('orders')").fetchall()
    }
    assert "idx_orders_strategy_updated" in indexes
    storage.close()


def test_orders_endpoint_supports_incremental_updated_after_cursor(tmp_path: Path) -> None:
    from openpine.gateway.routes.orders_positions import list_orders

    storage = SQLiteStorage(tmp_path / "orders.sqlite")
    storage.execute(
        """
        CREATE TABLE orders(
            order_id TEXT, strategy_id TEXT, account_id TEXT,
            client_order_id TEXT, symbol TEXT, side TEXT, order_type TEXT,
            qty REAL, limit_price REAL, stop_price REAL, take_profit_price REAL,
            status TEXT, filled_quantity REAL, avg_fill_price REAL, error TEXT,
            created_at INTEGER, updated_at INTEGER
        )
        """
    )
    storage.execute_many(
        "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("old", "s1", None, None, "BTCUSDT", "buy", "market", 1, None, None, None, "filled", 1, 10, None, 100, 100),
            ("new", "s1", None, None, "BTCUSDT", "buy", "market", 1, None, None, None, "filled", 1, 11, None, 200, 200),
        ],
    )
    storage.commit()

    rows = asyncio.run(
        list_orders(strategy_id="s1", updated_after=100, state=SimpleNamespace(storage=storage))
    )

    assert [row["order_id"] for row in rows] == ["new"]
    storage.close()


def test_sqlite_storage_serializes_compound_transactions_across_threads(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "transactions.sqlite")
    storage.execute("CREATE TABLE events(value INTEGER)")
    storage.commit()
    first_inserted = threading.Event()
    errors: list[BaseException] = []

    def rolling_back_writer() -> None:
        try:
            with storage.transaction():
                storage.execute("INSERT INTO events(value) VALUES (1)")
                first_inserted.set()
                time.sleep(0.2)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    def committed_writer() -> None:
        first_inserted.wait(timeout=2)
        try:
            with storage.transaction():
                storage.execute("INSERT INTO events(value) VALUES (2)")
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    thread_a = threading.Thread(target=rolling_back_writer)
    thread_b = threading.Thread(target=committed_writer)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=3)
    thread_b.join(timeout=3)

    assert not errors
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert storage.execute("SELECT value FROM events ORDER BY value").fetchall() == [(2,)]
    storage.close()
