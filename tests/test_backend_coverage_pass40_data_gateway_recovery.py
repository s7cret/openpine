from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException
from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.data import candle_storage as cs
from openpine.data.candle_storage import CandleStorage
from openpine.data.contracts import WriteMode
from openpine.gateway.routes import pine_sources
from openpine.gateway.schemas import PineSourceCreate, PineSourceUpdate
from openpine.recovery.rebuild import StateRebuilder
from openpine.state.errors import StateInconsistencyError


def _bar(t: int, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close + 1, close - 1, close, 10.0, True)


def _query(start: int = 0, end: int = 180_000) -> BarQuery:
    return BarQuery(
        InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        parse_timeframe("1m"),
        start,
        end,
        source="storage",
        gap_policy="allow_with_metadata",
    )


def test_candle_storage_identity_checksum_read_and_gap_edges(tmp_path, monkeypatch):
    # Force the fallback hash branches even if xxhash is installed in the environment.
    monkeypatch.setitem(sys.modules, "xxhash", None)
    df = pd.DataFrame([{"open_time": 1, "close": 2.0}])
    assert len(cs._compute_checksum(df)) == 16
    assert len(cs._compute_schema_hash()) == 16

    # Invalid identity via missing/invalid candle attribute is returned as a WriteResult error.
    storage = CandleStorage(data_root=tmp_path, sqlite_path=tmp_path / "candles.sqlite")
    result = storage.write_candles([SimpleNamespace(time=0, time_close=60_000, open=1, high=1, low=1, close=1, volume=1)])
    assert result.success is False and "instrument_key is required" in result.error
    result = storage.write_candles(
        [SimpleNamespace(instrument_key="bad", time=0, time_close=60_000, open=1, high=1, low=1, close=1, volume=1)]
    )
    assert result.success is False and "Invalid instrument_key" in result.error

    # Empty write and replace-partition filename path.
    assert storage.write_candles([], instrument_key="binance:spot:BTCUSDT:trade").rows_written == 0
    written = storage.write_candles(
        [_bar(0), _bar(60_000), _bar(120_000)],
        mode=WriteMode.REPLACE_PARTITION,
        instrument_key="binance:spot:BTCUSDT:trade",
        timeframe="1m",
    )
    assert written.success and written.rows_written == 3

    # Missing partition path branch.
    query = _query()
    manifest = storage.list_manifests(query)[0]
    Path(manifest.partition_path).unlink()
    with pytest.raises(Exception, match="candle partition missing"):
        storage.read_candles(query)

    # Existing manifest but no rows in requested window branch.
    storage = CandleStorage(data_root=tmp_path / "second", sqlite_path=tmp_path / "second.sqlite")
    storage.write_candles([_bar(0)], instrument_key="binance:spot:BTCUSDT:trade", timeframe="1m")
    storage._get_conn().execute("UPDATE candle_manifests SET max_open_time = ?", (240_000,))
    storage._get_conn().commit()
    with pytest.raises(Exception, match="no candle rows"):
        storage.read_candles(_query(120_000, 180_000))

    # Gap detection: no manifests means whole range gap; separated manifests produce an internal gap.
    empty_storage = CandleStorage(data_root=tmp_path / "empty", sqlite_path=tmp_path / "empty.sqlite")
    whole_gap = empty_storage.detect_gaps(_query(0, 60_000))
    assert whole_gap[0].gap_start == 0 and whole_gap[0].gap_end == 60_000

    gap_storage = CandleStorage(data_root=tmp_path / "gaps", sqlite_path=tmp_path / "gaps.sqlite")
    gap_storage.write_candles([_bar(0)], instrument_key="binance:spot:BTCUSDT:trade", timeframe="1m")
    gap_storage.write_candles([_bar(180_000)], instrument_key="binance:spot:BTCUSDT:trade", timeframe="1m")
    gaps = gap_storage.detect_gaps(_query(0, 240_000))
    assert gaps and gaps[0].gap_start == 0 and gaps[0].gap_end == 180_000
    gap_storage.close()
    assert gap_storage._conn is None


class PineRegistry:
    def __init__(self):
        self.source = SimpleNamespace(
            id="p1",
            name="source",
            source_type="strategy",
            version="1",
            source_text="strategy('x')",
            source_hash="h",
            active_artifact_id="art1",
            created_at=1,
            updated_at=2,
        )
        self._mem = {"p1": self.source}
        self.removed: list[str] = []
        self._conn = SimpleNamespace(execute=lambda *args, **kwargs: None, commit=lambda: None)

    def list_sources(self):
        return list(self._mem.values())

    def get_source(self, source_id):
        if source_id == "p1" or source_id == "source":
            return self.source
        raise KeyError(source_id)

    def add_source(self, source_text, name):
        src = SimpleNamespace(
            id="p2",
            name=name,
            source_type="strategy",
            version="1",
            source_text=source_text,
            source_hash="h2",
            active_artifact_id=None,
            created_at=3,
            updated_at=4,
        )
        self._mem[src.id] = src
        return src

    def remove_source(self, source_id):
        self.removed.append(source_id)
        self._mem.pop(source_id, None)


class Storage:
    def __init__(self, *, fail_execute=False, fail_commit=False, count_value=2):
        self.fail_execute = fail_execute
        self.fail_commit = fail_commit
        self.count_value = count_value
        self.executed: list[str] = []
        self.rollbacks = 0

    def execute(self, sql, params=()):
        self.executed.append(sql)
        if self.fail_execute:
            raise RuntimeError("db boom")
        return SimpleNamespace(fetchone=lambda: (self.count_value,))

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit boom")

    def rollback(self):
        self.rollbacks += 1


def test_pine_source_routes_duplicate_missing_delete_and_preview_edges(tmp_path):
    registry = PineRegistry()

    with pytest.raises(HTTPException) as dup:
        asyncio.run(pine_sources.create_source(PineSourceCreate(name="source", source_text="indicator('x')"), registry))
    assert dup.value.status_code == 409

    with pytest.raises(HTTPException) as missing_update:
        asyncio.run(pine_sources.update_source("missing", PineSourceUpdate(name="x"), registry))
    assert missing_update.value.status_code == 404

    partial = asyncio.run(pine_sources.update_source("p1", PineSourceUpdate(source_type="indicator"), registry))
    assert partial.source_type == "indicator"

    artifact_root = tmp_path / "artifacts"
    source_dir = artifact_root / "p1"
    source_dir.mkdir(parents=True)
    (source_dir / "a.txt").write_text("artifact", encoding="utf-8")

    state = SimpleNamespace(
        pine_registry=registry,
        storage=Storage(fail_commit=True),
        artifact_store=SimpleNamespace(_source_dir=lambda source_id: source_dir),
    )
    with pytest.raises(HTTPException) as failed_delete:
        asyncio.run(pine_sources.delete_source("p1", state))
    assert failed_delete.value.status_code == 500
    assert registry.removed == []
    assert source_dir.exists()
    assert state.storage.rollbacks == 1

    with pytest.raises(HTTPException) as missing_delete:
        asyncio.run(pine_sources.delete_source("missing", state))
    assert missing_delete.value.status_code == 404

    preview_state = SimpleNamespace(
        pine_registry=PineRegistry(),
        storage=Storage(fail_execute=True),
        artifact_store=SimpleNamespace(_source_dir=lambda source_id: (_ for _ in ()).throw(RuntimeError("artifact boom"))),
    )
    preview = asyncio.run(pine_sources.delete_source_preview("p1", preview_state))
    assert preview["resources"]["compile_artifacts"] == 0
    assert preview["resources"]["artifact_files"] == 0

    with pytest.raises(HTTPException) as missing_preview:
        asyncio.run(pine_sources.delete_source_preview("missing", preview_state))
    assert missing_preview.value.status_code == 404


def test_state_rebuilder_success_error_verify_and_invalidate_edges():
    snap_before = SimpleNamespace(status="active", bar_time=10)
    snap_after = SimpleNamespace(status="active", bar_time=50)
    loaded_state = SimpleNamespace(strategy_id="s1", instrument_key="BTCUSDT", timeframe="1m", bar_time=10)

    class Store:
        def __init__(self, *, loaded=loaded_state, saved=object()):
            self.loaded = loaded
            self.saved = saved
            self.snapshots = [snap_before, snap_after]
            self.saved_calls = []

        def list_snapshots(self, strategy_id):
            return self.snapshots

        def load_snapshot(self, strategy_id):
            return self.loaded

        def save_snapshot(self, state, reason, failed_bar):
            self.saved_calls.append((state.bar_time, reason, failed_bar))
            return self.saved

    class Data:
        def get_bars(self, **kwargs):
            return [SimpleNamespace(time=20), SimpleNamespace(time=30)]

    class Engine:
        def __init__(self):
            self.seen = []

        def process_next_bar(self, **kwargs):
            self.seen.append(kwargs["bar"].time)

    engine = Engine()
    store = Store()
    state = StateRebuilder(store, Data(), engine).rebuild("s1", 40, reason="repair")
    assert state.bar_time == 30
    assert engine.seen == [20, 30]
    assert store.saved_calls == [(30, "repair", False)]

    with pytest.raises(StateInconsistencyError, match="No compatible snapshot"):
        StateRebuilder(Store(), Data()).rebuild("s1", 5)

    with pytest.raises(StateInconsistencyError, match="No loadable snapshot"):
        StateRebuilder(Store(loaded=None), Data()).rebuild("s1", 40)

    with pytest.raises(StateInconsistencyError, match="could not save"):
        StateRebuilder(Store(saved=None), Data()).rebuild("s1", 40)

    assert StateRebuilder(Store(loaded=SimpleNamespace(strategy_id="s1", bar_time=1))).verify_state("s1") is True
    assert StateRebuilder(Store(loaded=None)).verify_state("s1") is False
    assert StateRebuilder(Store(loaded=SimpleNamespace(strategy_id="", bar_time=1))).verify_state("s1") is False
    assert StateRebuilder(Store(loaded=SimpleNamespace(strategy_id="s1", bar_time=0))).verify_state("s1") is False

    class RaisingStore(Store):
        def load_snapshot(self, strategy_id):
            raise RuntimeError("bad snapshot")

    assert StateRebuilder(RaisingStore()).verify_state("s1") is False

    store = Store()
    StateRebuilder(store).invalidate("s1", since_bar_time=50)
    assert snap_before.status == "active" and snap_after.status == "invalid"
    StateRebuilder(store).invalidate("s1")
    assert snap_before.status == "invalid"
