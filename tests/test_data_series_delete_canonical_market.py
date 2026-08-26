from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import accounts_data


class _Storage:
    def execute(self, sql: str, params: tuple[object, ...] = ()):
        if "FROM candle_manifests" in sql:
            return SimpleNamespace(fetchall=lambda: [])
        raise AssertionError(sql)

    @contextmanager
    def transaction(self):
        yield


def _state(tmp_path: Path, stored_market: str) -> SimpleNamespace:
    cache_root = tmp_path / "cache"
    marketdata_root = cache_root / "marketdata"
    marketdata_root.mkdir(parents=True)
    index_path = marketdata_root / "index.sqlite"
    with closing(sqlite3.connect(index_path)) as db, db:
        db.execute(
            """
            CREATE TABLE marketdata_segments (
                id INTEGER PRIMARY KEY,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                start_time INTEGER NOT NULL,
                end_time INTEGER NOT NULL,
                rows_count INTEGER NOT NULL,
                source_transport TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                checksum TEXT NOT NULL,
                downloaded_at INTEGER NOT NULL,
                data_format TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO marketdata_segments VALUES
            (1, 'bybit', ?, 'SOLUSDT', '1m', 1000, 2000, 2,
             'rest', 'trade_kline', 'sha', 0, 'csv')
            """,
            (stored_market,),
        )
        db.execute(
            """
            INSERT INTO marketdata_segments VALUES
            (2, 'bybit', ?, 'SOLUSDT', '1m', 1000, 2000, 2,
             'rest', 'mark_kline', 'mark-sha', 0, 'csv')
            """,
            (stored_market,),
        )
    series_root = (
        marketdata_root
        / "v1"
        / "exchange=bybit"
        / f"market={stored_market}"
        / "symbol=SOLUSDT"
    )
    segment_dir = series_root / "source=trade_kline" / "timeframe=1m"
    segment_dir.mkdir(parents=True)
    (segment_dir / "bars.csv").write_text("time,open\n1000,1\n", encoding="utf-8")
    mark_dir = series_root / "source=mark_kline" / "timeframe=1m"
    mark_dir.mkdir(parents=True)
    (mark_dir / "bars.csv").write_text("time,open\n1000,2\n", encoding="utf-8")
    return SimpleNamespace(
        config=SimpleNamespace(
            data_cache_root=cache_root,
            data_dir=tmp_path / "data",
            sqlite_path=tmp_path / "openpine.sqlite",
        ),
        storage=_Storage(),
    )


@pytest.mark.parametrize("stored_market", ["linear", "usdm"])
def test_delete_canonical_futures_series_removes_storage_alias_and_invalidates_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_market: str,
) -> None:
    state = _state(tmp_path, stored_market)
    persistent_cache = tmp_path / "persistent-cache"
    persistent_cache.mkdir()
    monkeypatch.setattr(accounts_data, "default_cache_dir", lambda: persistent_cache)
    monkeypatch.chdir(tmp_path)
    series = accounts_data._data_series_inventory(state)
    assert len(series) == 2
    trade_series = next(item for item in series if item["price_type"] == "trade")
    mark_series = next(item for item in series if item["price_type"] == "mark_kline")
    assert trade_series["market_type"] == "futures"
    series_id = str(trade_series["id"])
    accounts_data._DATA_SUMMARY_CACHE = (
        accounts_data._data_summary_cache_key(state),
        1.0,
        {"series": series},
    )

    result = asyncio.run(accounts_data.delete_data_series(series_id, state))

    assert result["status"] == "deleted"
    assert int(result["marketdata_files"]) >= 2
    remaining_series = accounts_data._data_series_inventory(state)
    assert [item["id"] for item in remaining_series] == [mark_series["id"]]
    assert accounts_data._DATA_SUMMARY_CACHE is None
    index_path = state.config.data_cache_root / "marketdata" / "index.sqlite"
    with closing(sqlite3.connect(index_path)) as db, db:
        assert db.execute("SELECT source_kind FROM marketdata_segments").fetchall() == [
            ("mark_kline",)
        ]
    remaining_data = list((state.config.data_cache_root / "marketdata").rglob("bars.csv"))
    assert len(remaining_data) == 1
    assert "source=mark_kline" in str(remaining_data[0])


def test_delete_series_fails_closed_when_storage_still_contains_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = {
        "id": "series-1",
        "exchange": "bybit",
        "market_type": "futures",
        "symbol": "SOLUSDT",
        "price_type": "trade",
        "timeframe": "1m",
    }
    state = SimpleNamespace()
    monkeypatch.setattr(accounts_data, "_series_by_id", lambda state: {"series-1": series})
    monkeypatch.setattr(accounts_data, "_delete_persistent_cache_series", lambda series: 0)
    monkeypatch.setattr(accounts_data, "_delete_marketdata_segment_series", lambda state, series: 0)
    monkeypatch.setattr(accounts_data, "_delete_candle_manifest_series", lambda state, series: 0)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(accounts_data.delete_data_series("series-1", state))

    assert exc_info.value.status_code == 409
    assert "still present" in str(exc_info.value.detail)


def test_mark_series_does_not_delete_legacy_trade_persistent_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "persistent-cache"
    cache_dir.mkdir()
    metadata = cache_dir / "trade.json"
    metadata.write_text(
        json.dumps(
            {
                "key": {
                    "instrument": {
                        "exchange": "bybit",
                        "market": "linear",
                        "symbol": "SOLUSDT",
                    },
                    "timeframe": "1m",
                }
            }
        ),
        encoding="utf-8",
    )
    csv_path = metadata.with_suffix(".csv")
    csv_path.write_text("time,open\n1000,1\n", encoding="utf-8")
    monkeypatch.setattr(accounts_data, "default_cache_dir", lambda: cache_dir)

    deleted = accounts_data._delete_persistent_cache_series(
        {
            "exchange": "bybit",
            "market_type": "futures",
            "symbol": "SOLUSDT",
            "price_type": "mark",
            "timeframe": "1m",
        }
    )

    assert deleted == 0
    assert metadata.exists()
    assert csv_path.exists()


def test_manifest_only_mark_series_does_not_default_to_trade_kline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root = tmp_path / "cache"
    marketdata_root = cache_root / "marketdata"
    trade_dir = (
        marketdata_root
        / "v1"
        / "exchange=bybit"
        / "market=linear"
        / "symbol=SOLUSDT"
        / "source=trade_kline"
        / "timeframe=1m"
    )
    trade_dir.mkdir(parents=True)
    trade_file = trade_dir / "bars.csv"
    trade_file.write_text("time,open\n1000,1\n", encoding="utf-8")
    state = SimpleNamespace(
        config=SimpleNamespace(data_cache_root=cache_root, data_dir=tmp_path / "data")
    )
    monkeypatch.chdir(tmp_path)

    deleted = accounts_data._delete_marketdata_segment_series(
        state,
        {
            "exchange": "bybit",
            "market_type": "futures",
            "symbol": "SOLUSDT",
            "price_type": "mark",
            "timeframe": "1m",
        },
    )

    assert deleted == 0
    assert trade_file.exists()
