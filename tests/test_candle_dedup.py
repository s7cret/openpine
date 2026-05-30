"""Test candle deduplication, compaction, and idempotent backfill."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def test_read_candles_without_manifests_fails_closed():
    from openpine.data.candle_storage import CandleStorage
    from openpine.data.orchestrator import StorageUnavailableError
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    storage = CandleStorage()
    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="NO_MANIFEST_TEST"),
        timeframe=parse_timeframe("1h"),
        start_ms=1704067200000,
        end_ms=1704070800000,
        source="storage",
    )

    with pytest.raises(StorageUnavailableError, match="no candle manifests"):
        storage.read_candles(query)


def test_read_candles_deduplicates_identical_rows():
    """CandleStorage.read_candles must return unique open_time values
    even when multiple manifests contain identical duplicates."""
    from openpine.data.candle_storage import CandleStorage
    from openpine.data.models import CandleManifest
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    storage = CandleStorage()

    # Create two manifests with overlapping identical data
    manifest_a = CandleManifest(
        manifest_id="m_a",
        exchange="binance",
        market_type="spot",
        symbol="TESTDEDUP",
        price_type="trade",
        timeframe="1h",
        partition_path="/tmp/test_dedup_a.parquet",
        min_open_time=1704067200000,
        max_open_time=1704153600000,
        row_count=2,
        schema_hash="test",
        checksum="test",
        file_size_bytes=100,
        provider="binance",
        ingested_at=0,
        created_at=0,
        updated_at=0,
    )
    manifest_b = CandleManifest(
        manifest_id="m_b",
        exchange="binance",
        market_type="spot",
        symbol="TESTDEDUP",
        price_type="trade",
        timeframe="1h",
        partition_path="/tmp/test_dedup_b.parquet",
        min_open_time=1704067200000,
        max_open_time=1704153600000,
        row_count=2,
        schema_hash="test",
        checksum="test",
        file_size_bytes=100,
        provider="binance",
        ingested_at=0,
        created_at=0,
        updated_at=0,
    )

    # Write two parquet files with identical overlapping data
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    df_a = pd.DataFrame({
        "exchange": ["binance", "binance"],
        "market_type": ["spot", "spot"],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "price_type": ["trade", "trade"],
        "timeframe": ["1h", "1h"],
        "open_time": [1704067200000, 1704153600000],
        "close_time": [1704070800000, 1704157200000],
        "open": [100.0, 101.0],
        "high": [101.0, 102.0],
        "low": [99.0, 100.0],
        "close": [100.5, 101.5],
        "volume": [10.0, 11.0],
        "quote_volume": [None, None],
        "trades_count": [None, None],
        "is_closed": [True, True],
        "source": ["test", "test"],
        "provider": ["binance", "binance"],
        "ingested_at": [0, 0],
    })
    df_b = df_a.copy()  # identical data

    from openpine.data.candle_storage import PARQUET_SCHEMA
    pq.write_table(pa.Table.from_pandas(df_a, schema=PARQUET_SCHEMA, preserve_index=False), "/tmp/test_dedup_a.parquet")
    pq.write_table(pa.Table.from_pandas(df_b, schema=PARQUET_SCHEMA, preserve_index=False), "/tmp/test_dedup_b.parquet")

    # Insert manifests
    storage._insert_manifest(manifest_a)
    storage._insert_manifest(manifest_b)

    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="TESTDEDUP"),
        timeframe=parse_timeframe("1h"),
        start_ms=1704067200000,
        end_ms=1704157200000,
        source="storage",
    )
    bars = storage.read_candles(query)

    # Should return only 2 unique bars, not 4
    assert len(bars) == 2, f"Expected 2 canonical bars, got {len(bars)}"
    assert bars[0].time == 1704067200000
    assert bars[1].time == 1704153600000

    # Cleanup
    import os
    os.unlink("/tmp/test_dedup_a.parquet")
    os.unlink("/tmp/test_dedup_b.parquet")
    conn = storage._get_conn()
    conn.execute("DELETE FROM candle_manifests WHERE manifest_id IN ('m_a', 'm_b')")
    conn.commit()


def test_conflicting_duplicate_detection():
    """Data doctor must detect conflicting duplicates (same open_time,
    different OHLCV values)."""
    from openpine.data.candle_storage import CandleStorage
    from openpine.data.models import CandleManifest
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    storage = CandleStorage()

    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from openpine.data.candle_storage import PARQUET_SCHEMA

    df_a = pd.DataFrame({
        "exchange": ["binance"],
        "market_type": ["spot"],
        "symbol": ["BTCUSDT"],
        "price_type": ["trade"],
        "timeframe": ["1h"],
        "open_time": [1704067200000],
        "close_time": [1704070800000],
        "open": [100.0],
        "high": [101.0],
        "low": [99.0],
        "close": [100.5],
        "volume": [10.0],
        "quote_volume": [None],
        "trades_count": [None],
        "is_closed": [True],
        "source": ["test"],
        "provider": ["binance"],
        "ingested_at": [0],
    })
    df_b = df_a.copy()
    df_b["close"] = [200.0]  # conflicting value

    pq.write_table(pa.Table.from_pandas(df_a, schema=PARQUET_SCHEMA, preserve_index=False), "/tmp/test_conflict_a.parquet")
    pq.write_table(pa.Table.from_pandas(df_b, schema=PARQUET_SCHEMA, preserve_index=False), "/tmp/test_conflict_b.parquet")

    manifest_a = CandleManifest(
        manifest_id="m_conflict_a",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1h",
        partition_path="/tmp/test_conflict_a.parquet",
        min_open_time=1704067200000,
        max_open_time=1704067200000,
        row_count=1,
        schema_hash="test",
        checksum="test_a",
        file_size_bytes=100,
        provider="binance",
        ingested_at=0,
        created_at=0,
        updated_at=0,
    )
    manifest_b = CandleManifest(
        manifest_id="m_conflict_b",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1h",
        partition_path="/tmp/test_conflict_b.parquet",
        min_open_time=1704067200000,
        max_open_time=1704067200000,
        row_count=1,
        schema_hash="test",
        checksum="test_b",
        file_size_bytes=100,
        provider="binance",
        ingested_at=0,
        created_at=0,
        updated_at=0,
    )

    storage._insert_manifest(manifest_a)
    storage._insert_manifest(manifest_b)

    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1h"),
        start_ms=1704067200000,
        end_ms=1704070800000,
        source="storage",
    )

    # read_candles should detect conflict but still return one bar (first seen)
    bars = storage.read_candles(query)
    assert len(bars) == 1

    # Cleanup
    import os
    os.unlink("/tmp/test_conflict_a.parquet")
    os.unlink("/tmp/test_conflict_b.parquet")
    conn = storage._get_conn()
    conn.execute("DELETE FROM candle_manifests WHERE manifest_id LIKE 'm_conflict_%'")
    conn.commit()


def test_list_manifests_filters_inactive():
    """list_manifests must not return superseded (inactive) manifests."""
    from openpine.data.candle_storage import CandleStorage
    from openpine.data.models import CandleManifest
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    storage = CandleStorage()

    manifest_active = CandleManifest(
        manifest_id="m_active",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1h",
        partition_path="/tmp/test_active.parquet",
        min_open_time=1704067200000,
        max_open_time=1704153600000,
        row_count=2,
        schema_hash="test",
        checksum="test",
        file_size_bytes=100,
        provider="binance",
        ingested_at=0,
        created_at=0,
        updated_at=0,
    )
    manifest_inactive = CandleManifest(
        manifest_id="m_inactive",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1h",
        partition_path="/tmp/test_inactive.parquet",
        min_open_time=1704067200000,
        max_open_time=1704153600000,
        row_count=2,
        schema_hash="test",
        checksum="test",
        file_size_bytes=100,
        provider="binance",
        ingested_at=0,
        created_at=0,
        updated_at=0,
    )

    storage._insert_manifest(manifest_active)
    storage._insert_manifest(manifest_inactive)

    # Mark one as inactive
    conn = storage._get_conn()
    conn.execute("UPDATE candle_manifests SET is_active = 0 WHERE manifest_id = 'm_inactive'")
    conn.commit()

    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1h"),
        start_ms=1704067200000,
        end_ms=1704157200000,
        source="storage",
    )
    manifests = storage.list_manifests(query)
    ids = [m.manifest_id for m in manifests]

    assert "m_active" in ids
    assert "m_inactive" not in ids

    # Cleanup
    conn.execute("DELETE FROM candle_manifests WHERE manifest_id IN ('m_active', 'm_inactive')")
    conn.commit()
