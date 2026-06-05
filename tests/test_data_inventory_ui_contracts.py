from __future__ import annotations

from types import SimpleNamespace

from openpine.gateway.routes.accounts_data import _orders_summary, _series_role
from openpine.storage import MigrationRunner, SQLiteStorage


def test_series_role_marks_one_minute_as_source() -> None:
    assert _series_role({"timeframe": "1m", "sources": ["marketdata_store"]}) == "source"
    assert _series_role({"timeframe": "15m", "sources": ["marketdata_store"]}) == "derived"


def test_orders_summary_groups_by_strategy_name(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    try:
        MigrationRunner().run_migrations(storage)
        storage.execute(
            """
            INSERT INTO pine_sources
            (id, pine_id, name, source_text, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("pine-1", "pine-1", "source", 'strategy("x")', "strategy", 1, 1),
        )
        storage.execute(
            """
            INSERT INTO compile_artifacts
            (id, source_id, params_hash, artifact_path, compile_meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("artifact-1", "pine-1", "h", "/tmp/artifact.py", "{}", 1),
        )
        storage.execute(
            """
            INSERT INTO strategy_instances
            (id, strategy_id, name, pine_id, artifact_id, symbol, timeframe, exchange, market_type,
             params_json, params_hash, mode, enabled, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("strat-1", "strat-1", "EMA Live", "pine-1", "artifact-1", "BTCUSDT", "1m", "binance", "spot", "{}", "h", "paper", 1, "running", 1, 1),
        )
        storage.execute(
            """
            INSERT INTO orders
            (order_id, strategy_id, account_id, client_order_id, symbol, side, order_type, qty,
             status, intent_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ord-1", "strat-1", "default", "client-1", "BTCUSDT", "buy", "market", 1.0, "filled", "{}", 1000, 1000),
        )
        storage.commit()

        summary = _orders_summary(SimpleNamespace(storage=storage))
    finally:
        storage.close()

    assert summary["total"] == 1
    assert summary["by_strategy"][0]["strategy_id"] == "strat-1"
    assert summary["by_strategy"][0]["strategy_name"] == "EMA Live"
    assert summary["by_strategy"][0]["status"] == "filled"
