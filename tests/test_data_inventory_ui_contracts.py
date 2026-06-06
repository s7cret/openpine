from __future__ import annotations

from types import SimpleNamespace

from openpine.gateway.routes import accounts_data as data_routes
from openpine.gateway.routes.accounts_data import _orders_summary, _series_role, _store_backfill_series
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


def test_backfill_stores_series_in_one_bulk_write() -> None:
    from marketdata_provider.contracts import (
        Bar,
        BarQuery,
        BarSeries,
        CoverageReport,
        InstrumentKey,
        StoreResult,
        parse_timeframe,
    )

    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    bars = tuple(
        Bar(
            instrument=instrument,
            timeframe=timeframe,
            time=index * 60_000,
            time_close=(index + 1) * 60_000,
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
            closed=True,
        )
        for index in range(3)
    )
    query = BarQuery(
        instrument=instrument,
        timeframe=timeframe,
        start_ms=0,
        end_ms=180_000,
        source="provider",
        gap_policy="allow_with_metadata",
    )
    series = BarSeries(
        query=query,
        bars=bars,
        coverage=CoverageReport(0, 180_000, 0, 180_000, source_mix=("provider",)),
    )
    existing = Bar(
        instrument=instrument,
        timeframe=timeframe,
        time=60_000,
        time_close=120_000,
        open=9.0,
        high=9.0,
        low=9.0,
        close=9.0,
        volume=9.0,
        closed=True,
    )
    calls = []

    class FakeOrchestrator:
        def load_bars(self, storage_query):
            return BarSeries(
                query=storage_query,
                bars=(existing,),
                coverage=CoverageReport(0, 180_000, 60_000, 120_000, source_mix=("storage",)),
            )

        def store_bars(self, stored_series):
            calls.append(stored_series)
            return StoreResult(success=True, rows_written=2)

    loaded, skipped = _store_backfill_series(SimpleNamespace(orchestrator=FakeOrchestrator()), series)

    assert len(calls) == 1
    assert [bar.time for bar in calls[0].bars] == [0, 120_000]
    assert loaded == 2
    assert skipped == 1


def test_backfill_fast_skips_when_inventory_covers_request(monkeypatch) -> None:
    payload = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "PEPEUSDT",
        "timeframe": "1m",
        "from_time": 0,
        "to_time": 180_000,
    }

    def merge_cache(groups):
        entry = data_routes._series_entry(groups, ("binance", "spot", "PEPEUSDT", "trade", "1m"))
        entry["ranges"] = [{"from_ms": 0, "to_ms": 120_000, "rows": 3, "source": "persistent_cache"}]

    monkeypatch.setattr(data_routes, "_merge_persistent_cache_groups", merge_cache)
    monkeypatch.setattr(data_routes, "_merge_marketdata_segment_groups", lambda state, groups: None)
    monkeypatch.setattr(data_routes, "_merge_candle_manifest_groups", lambda state, groups: None)

    class FakeOrchestrator:
        def load_bars(self, query, progress_callback=None):
            raise AssertionError("covered backfill should not fetch provider bars")

    result = data_routes._run_data_backfill_sync(
        payload,
        SimpleNamespace(orchestrator=FakeOrchestrator()),
        lambda *args: None,
    )

    assert result["fast_skipped"] is True
    assert result["bars_loaded"] == 0
    assert result["skipped_existing"] == 3
    assert result["coverage_complete"] is True
