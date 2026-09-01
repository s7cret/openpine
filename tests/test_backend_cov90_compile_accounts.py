from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from openpine.gateway.routes import accounts_data as ad


class _Console:
    def __init__(self):
        self.messages=[]
    def print(self,*a,**k):
        self.messages.append(" ".join(map(str,a)))






def test_accounts_data_inventory_delete_and_routes(tmp_path, monkeypatch):
    default_cache = tmp_path / "cache"
    default_cache.mkdir()
    meta = {
        "key": {"instrument": {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"}, "timeframe": "1m"},
        "rows": 2,
        "first_time": 0,
        "last_time": 60_000,
    }
    (default_cache / "a.json").write_text(json.dumps(meta))
    (default_cache / "a.csv").write_text("x")
    (default_cache / "bad.json").write_text("{")
    monkeypatch.setattr(ad, "default_cache_dir", lambda: default_cache)

    db_path = tmp_path / "db.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE orders(order_id TEXT, strategy_id TEXT, symbol TEXT, status TEXT, created_at INTEGER)")
    db.execute("CREATE TABLE fills(fill_id TEXT, order_id TEXT)")
    db.execute("CREATE TABLE strategy_instances(strategy_id TEXT, name TEXT)")
    db.execute("CREATE TABLE candle_manifests(manifest_id TEXT, exchange TEXT, market_type TEXT, symbol TEXT, price_type TEXT, timeframe TEXT, min_open_time INTEGER, max_open_time INTEGER, row_count INTEGER, file_size_bytes INTEGER, partition_path TEXT, is_active INTEGER)")
    p = tmp_path / "partition.parquet"; p.write_text("x")
    db.execute("INSERT INTO candle_manifests VALUES ('m','binance','spot','BTCUSDT','trade','1m',0,60000,2,10,?,1)", (str(p),))
    db.execute("INSERT INTO orders VALUES ('o','s','BTCUSDT','open',123)")
    db.execute("INSERT INTO fills VALUES ('f','o')")
    db.execute("INSERT INTO strategy_instances VALUES ('s','Strategy')")
    db.commit(); db.close()

    class Storage:
        def __init__(self): self.db=sqlite3.connect(db_path)
        def execute(self, *a): return self.db.execute(*a)
        def transaction(self): return self.db
    state = SimpleNamespace(config=SimpleNamespace(sqlite_path=db_path, data_dir=tmp_path, data_cache_root=tmp_path/"root"), storage=Storage())

    groups = {}
    ad._merge_persistent_cache_groups(groups)
    assert groups and next(iter(groups.values()))["bar_count"] == 2
    (state.config.data_cache_root / "marketdata").mkdir(parents=True)
    mroot = state.config.data_cache_root / "marketdata"
    index = mroot / "index.sqlite"
    con = sqlite3.connect(index)
    con.execute("CREATE TABLE marketdata_segments(id TEXT, exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT, start_time INTEGER, end_time INTEGER, rows_count INTEGER, source_kind TEXT)")
    con.execute("INSERT INTO marketdata_segments VALUES ('seg','binance','spot','BTCUSDT','1m',0,60000,2,'trade_kline')")
    con.commit(); con.close()
    segdir = ad._marketdata_segment_dir(mroot, "binance", "spot", "BTCUSDT", "1m", "trade_kline")
    segdir.mkdir(parents=True); (segdir/"x.parquet").write_text("x")
    ad._merge_marketdata_segment_groups(state, groups)
    ad._merge_candle_manifest_groups(state, groups)
    inv = ad._data_series_inventory(state)
    assert inv and inv[0]["symbol"] == "BTCUSDT"
    summary = ad._data_summary(state)
    assert summary["orders"]["total"] == 1
    byid = ad._series_by_id(state)
    series = next(iter(byid.values()))
    assert ad._compact_ranges([{"from_ms": i, "to_ms": i, "rows": 1} for i in range(8)])[3]["collapsed"] == 3
    assert ad._coalesce_ranges([{"from_ms": 0, "to_ms": 0, "rows": 1, "source": "a"}, {"from_ms": 60_000, "to_ms": 60_000, "rows": 1, "source": "b"}], "1m")[0]["source"] == "a,b"
    assert ad._estimate_unique_bars([{"from_ms": None, "to_ms": None, "rows": 7}], "1m") == 7
    assert ad._estimate_bars_for_window(100, 100, "1m") == 0
    assert ad._timeframe_duration_ms("bad") == 60_000
    assert ad._freshness_status(None, "1m") == "empty"
    assert ad._database_size_bytes(state) >= 0
    assert ad._persistent_cache_size_bytes() > 0
    assert ad._candle_store_size_bytes(state) >= 0

    deleted_cache = ad._delete_persistent_cache_series(series)
    assert deleted_cache >= 1
    deleted_market = ad._delete_marketdata_segment_series(state, series)
    assert deleted_market >= 1
    deleted_manifest = ad._delete_candle_manifest_series(state, series)
    assert deleted_manifest == 1
    assert ad._delete_candle_manifest_series(state, series) == 0
    state.storage.db.close()
