"""CandleStorage: read/write OHLCV parquet partitions.

Section OP-DL-004 of OpenPine.

Atomic write rules:
1. Write to .tmp/ partition
2. Validate schema and unique key
3. Atomic rename to final path
4. Update SQLite manifest ONLY after file commit succeeds
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marketdata_provider.contracts import Bar, BarQuery
from openpine.data.contracts import PARQUET_LAYOUT, WriteMode
from openpine.data.models import (
    CandleManifest,
    DataGap,
    WriteResult,
)

# Parquet schema
PARQUET_SCHEMA = pa.schema(
    [
        pa.field("exchange", pa.string()),
        pa.field("market_type", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("price_type", pa.string()),
        pa.field("timeframe", pa.string()),
        pa.field("open_time", pa.int64()),
        pa.field("close_time", pa.int64()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("quote_volume", pa.float64(), nullable=True),
        pa.field("trades_count", pa.int64(), nullable=True),
        pa.field("is_closed", pa.bool_()),
        pa.field("source", pa.string()),
        pa.field("provider", pa.string()),
        pa.field("ingested_at", pa.int64()),
    ]
)


def _compute_checksum(df: pd.DataFrame) -> str:
    """Compute xxhash for dataframe rows."""
    try:
        import xxhash

        return xxhash.xxh64(df.to_string()).hexdigest()
    except ImportError:
        return hashlib.sha256(df.to_string().encode()).hexdigest()[:16]


def _compute_schema_hash() -> str:
    """Compute hash of the parquet schema."""
    schema_str = str(PARQUET_SCHEMA)
    try:
        import xxhash

        return xxhash.xxh64(schema_str).hexdigest()
    except ImportError:
        return hashlib.sha256(schema_str.encode()).hexdigest()[:16]


def _storage_identity(query: BarQuery) -> tuple[str, str, str, str, str, int, int]:
    """Map canonical marketdata query to OpenPine's parquet partition identity."""

    return (
        query.instrument.exchange,
        query.instrument.market,
        query.instrument.symbol,
        "trade",
        query.timeframe.canonical,
        query.start_ms,
        query.end_ms,
    )


def _storage_instrument_key(query: BarQuery) -> str:
    exchange, market_type, symbol, price_type, _, _, _ = _storage_identity(query)
    return f"{exchange}:{market_type}:{symbol}:{price_type}"


def _storage_unavailable(message: str) -> Exception:
    from openpine.data.orchestrator import StorageUnavailableError

    return StorageUnavailableError(message)


def _utc_from_ms(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC)


def _parse_instrument_key(instrument_key: str) -> tuple[str, str, str, str]:
    parts = instrument_key.split(":")
    if len(parts) != 4:
        raise ValueError(f"Invalid instrument_key format: {instrument_key}")
    exchange, market_type, symbol, price_type = parts
    return exchange, market_type, symbol, price_type


def _resolve_write_identity(candles: list["Bar"], instrument_key: str | None) -> tuple[str, str, str, str]:
    if instrument_key:
        return _parse_instrument_key(instrument_key)

    first = candles[0]
    bar_instrument_key = getattr(first, "instrument_key", None)
    if bar_instrument_key is None:
        raise ValueError("instrument_key is required either as parameter or bar attribute")
    try:
        return _parse_instrument_key(bar_instrument_key)
    except ValueError as exc:
        raise ValueError(f"Invalid instrument_key: {bar_instrument_key}") from exc


def _bar_to_parquet_row(
    bar: "Bar",
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    price_type: str,
    timeframe: str,
    provider: str,
    ingested_at: int,
) -> dict:
    return {
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "price_type": price_type,
        "timeframe": timeframe,
        "open_time": bar.time,
        "close_time": bar.time_close or bar.time,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "quote_volume": None,
        "trades_count": None,
        "is_closed": True,
        "source": "openpine",
        "provider": provider,
        "ingested_at": ingested_at,
    }


def _partition_candles_by_month(
    candles: list["Bar"],
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    price_type: str,
    timeframe: str,
) -> dict[tuple[str, str, str, str, str, tuple[int, int]], list["Bar"]]:
    partitions: dict[tuple[str, str, str, str, str, tuple[int, int]], list["Bar"]] = {}
    for bar in candles:
        dt = _utc_from_ms(bar.time)
        key = (
            exchange,
            market_type,
            symbol,
            price_type,
            timeframe,
            (dt.year, dt.month),
        )
        partitions.setdefault(key, []).append(bar)
    return partitions


def _deduplicate_rows_by_open_time(rows: list[dict]) -> list[dict]:
    seen: dict[int, dict] = {}
    for row in rows:
        open_time = int(row["open_time"])
        if open_time in seen:
            continue
        seen[open_time] = row
    canonical_rows = list(seen.values())
    canonical_rows.sort(key=lambda r: r["open_time"])
    return canonical_rows


def _row_value_is_missing(value: object) -> bool:
    return value is None or bool(pd.isna(value))


def _row_to_bar(row: dict, query: BarQuery) -> Bar:
    close_time = row["close_time"]
    volume = row["volume"]
    open_time = int(row["open_time"])
    return Bar(
        instrument=query.instrument,
        timeframe=query.timeframe,
        time=open_time,
        time_close=open_time + query.timeframe.duration_ms if _row_value_is_missing(close_time) else int(close_time),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=0.0 if _row_value_is_missing(volume) else float(volume),
        closed=True,
    )


class CandleStorage:
    """CandleStorage manages OHLCV parquet partitions with SQLite manifest tracking.

    Layout:
        {configured_data_dir}/candles/
          exchange=binance/
            market_type=spot/
              symbol=BTCUSDT/
                price_type=trade/
                  timeframe=1m/
                    year=2026/
                      month=05/
                        part-000.parquet
    """

    def __init__(
        self,
        data_root: str | Path | None = None,
        sqlite_path: str | Path | None = None,
        provider: str = "binance",
    ) -> None:
        """Initialize CandleStorage.

        Args:
            data_root: Root directory for parquet candle data
            sqlite_path: Path to SQLite database
            provider: Default provider name
        """
        if data_root is None or sqlite_path is None:
            from openpine.config import OpenPineConfig

            config = OpenPineConfig.load()
            data_root = data_root or config.data_dir
            sqlite_path = sqlite_path or config.sqlite_path
        self.data_root = Path(os.path.expanduser(str(data_root)))
        self.sqlite_path = Path(os.path.expanduser(str(sqlite_path)))
        self.provider = provider
        self._conn: Optional[sqlite3.Connection] = None
        self._schema_hash = _compute_schema_hash()

    @property
    def candles_root(self) -> Path:
        """Root directory for candle parquet files."""
        return self.data_root / "candles"

    def _get_conn(self) -> sqlite3.Connection:
        """Lazy-open SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._ensure_schema(self._conn)
        return self._conn  # type: ignore[return-value]

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create the legacy manifest table for fresh configured stores."""

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candle_manifests (
                manifest_id TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price_type TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                partition_path TEXT NOT NULL UNIQUE,
                min_open_time INTEGER NOT NULL,
                max_open_time INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                schema_hash TEXT NOT NULL,
                checksum TEXT NOT NULL,
                file_size_bytes INTEGER,
                provider TEXT NOT NULL DEFAULT 'binance',
                ingested_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                superseded_by TEXT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candle_manifests_instrument
                ON candle_manifests(exchange, market_type, symbol, price_type, timeframe)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candle_manifests_time_range
                ON candle_manifests(
                    exchange, market_type, symbol, price_type, timeframe, min_open_time, max_open_time
                )
            """
        )
        conn.commit()

    def _partition_path(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        price_type: str,
        timeframe: str,
        open_time_ms: int,
    ) -> Path:
        """Compute the partition path for a given open_time.

        Path: exchange=X/market_type=Y/symbol=Z/price_type=W/timeframe=T/year=YYYY/month=MM/
        """
        dt = _utc_from_ms(open_time_ms)
        layout = PARQUET_LAYOUT.format(
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            price_type=price_type,
            timeframe=timeframe,
            year=dt.year,
            month=dt.month,
        )
        return self.candles_root / layout

    def _tmp_path(self, final_path: Path) -> Path:
        """Compute .tmp path for atomic write."""
        return final_path.parent / f".{final_path.name}.tmp"

    def write_candles(
        self,
        candles: list["Bar"],
        mode: WriteMode = WriteMode.UPSERT_PARTITION,
        instrument_key: str | None = None,
        timeframe: str = "1m",
    ) -> WriteResult:
        """Write candles to parquet storage atomically.

        Args:
            candles: List of Bar objects to write
            mode: Write mode (append_only, upsert_partition, repair_range, replace_partition)
            instrument_key: Instrument key (e.g. "binance:spot:BTCUSDT:trade").
                           If not provided, candles must have instrument_key attribute.
            timeframe: Timeframe string (e.g. "1m", "5m"). Required if instrument_key not provided.

        Returns:
            WriteResult with success status, row count, and manifest info
        """
        if not candles:
            return WriteResult(success=True, rows_written=0)

        try:
            exchange, market_type, symbol, price_type = _resolve_write_identity(candles, instrument_key)
        except ValueError as exc:
            return WriteResult(success=False, error=str(exc))

        partitions = _partition_candles_by_month(
            candles,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            price_type=price_type,
            timeframe=timeframe,
        )

        manifests: list[CandleManifest] = []
        total_rows = 0

        for (exchange, market_type, symbol, price_type, timeframe, (_year, _month)), bars in partitions.items():
            manifest = self._write_partition(
                bars=bars,
                mode=mode,
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                price_type=price_type,
                timeframe=timeframe,
            )

            # Insert manifest to SQLite
            self._insert_manifest(manifest)
            manifests.append(manifest)
            total_rows += manifest.row_count

        return WriteResult(
            success=True,
            rows_written=total_rows,
            partition_path=manifests[-1].partition_path if manifests else None,
            manifests_created=manifests,
        )

    def _write_partition(
        self,
        *,
        bars: list["Bar"],
        mode: WriteMode,
        exchange: str,
        market_type: str,
        symbol: str,
        price_type: str,
        timeframe: str,
    ) -> CandleManifest:
        first_bar = bars[0]
        partition_dir = self._partition_path(
            exchange, market_type, symbol, price_type, timeframe, first_bar.time
        )
        ts = int(time.time() * 1000)
        filename = (
            f"part-{ts:012d}.parquet"
            if mode == WriteMode.REPLACE_PARTITION
            else f"part-{ts}.parquet"
        )
        final_path = partition_dir / filename
        tmp_path = self._tmp_path(final_path)

        ingested_at = int(time.time() * 1000)
        df = pd.DataFrame(
            [
                _bar_to_parquet_row(
                    bar,
                    exchange=exchange,
                    market_type=market_type,
                    symbol=symbol,
                    price_type=price_type,
                    timeframe=timeframe,
                    provider=self.provider,
                    ingested_at=ingested_at,
                )
                for bar in bars
            ]
        ).sort_values("open_time")
        table = pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False)

        partition_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(tmp_path), compression="zstd")
        shutil.move(str(tmp_path), str(final_path))

        now = int(time.time() * 1000)
        return CandleManifest(
            manifest_id=f"m_{now}_{hashlib.sha256(final_path.name.encode()).hexdigest()[:8]}",
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            price_type=price_type,
            timeframe=timeframe,
            partition_path=str(final_path),
            min_open_time=int(df["open_time"].min()),
            max_open_time=int(df["open_time"].max()),
            row_count=len(df),
            schema_hash=self._schema_hash,
            checksum=_compute_checksum(df),
            file_size_bytes=final_path.stat().st_size,
            provider=self.provider,
            ingested_at=now,
            created_at=now,
            updated_at=now,
        )

    def _insert_manifest(self, manifest: CandleManifest) -> None:
        """Insert or replace a candle manifest in SQLite."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO candle_manifests
            (manifest_id, exchange, market_type, symbol, price_type, timeframe,
             partition_path, min_open_time, max_open_time, row_count, schema_hash,
             checksum, file_size_bytes, provider, ingested_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.manifest_id,
                manifest.exchange,
                manifest.market_type,
                manifest.symbol,
                manifest.price_type,
                manifest.timeframe,
                manifest.partition_path,
                manifest.min_open_time,
                manifest.max_open_time,
                manifest.row_count,
                manifest.schema_hash,
                manifest.checksum,
                manifest.file_size_bytes,
                manifest.provider,
                manifest.ingested_at,
                manifest.created_at,
                manifest.updated_at,
            ),
        )
        conn.commit()

    def read_candles(self, query: BarQuery) -> list["Bar"]:
        """Read candles matching the query.

        Args:
            query: BarQuery specifying instrument, timeframe, time range

        Returns:
            List of Bar objects matching the query
        """
        exchange, market_type, symbol, price_type, _, _, _ = _storage_identity(query)

        # Find matching manifest files
        manifests = self.list_manifests(query)
        if not manifests:
            raise _storage_unavailable(f"no candle manifests for query: {_storage_instrument_key(query)}")

        all_rows: list[dict] = []
        for m in manifests:
            pq_path = Path(m.partition_path)
            if not pq_path.exists():
                from openpine.data.orchestrator import StorageUnavailableError

                raise StorageUnavailableError(f"candle partition missing: {pq_path}")
            pf = pq.ParquetFile(str(pq_path))
            table = pf.read()
            df = table.to_pandas()

            # Apply time filter
            df = df[(df["open_time"] >= query.start_ms) & (df["open_time"] < query.end_ms)]

            all_rows.extend(df.to_dict("records"))

        if not all_rows:
            raise _storage_unavailable(f"no candle rows for query: {_storage_instrument_key(query)}")

        return [_row_to_bar(row, query) for row in _deduplicate_rows_by_open_time(all_rows)]

    def list_manifests(self, query: BarQuery) -> list[CandleManifest]:
        """List candle manifests matching the query.

        Args:
            query: BarQuery specifying instrument and time range

        Returns:
            List of CandleManifest objects
        """

        exchange, market_type, symbol, price_type, timeframe, start_ms, end_ms = _storage_identity(query)

        conn = self._get_conn()
        sql = """
            SELECT manifest_id, exchange, market_type, symbol, price_type, timeframe,
                   partition_path, min_open_time, max_open_time, row_count, schema_hash,
                   checksum, file_size_bytes, provider, ingested_at, created_at, updated_at
            FROM candle_manifests
            WHERE exchange = ? AND market_type = ? AND symbol = ? AND price_type = ?
              AND timeframe = ?
        """
        params: list = [exchange, market_type, symbol, price_type, timeframe]

        sql += " AND max_open_time >= ?"
        params.append(start_ms)
        sql += " AND min_open_time < ?"
        params.append(end_ms)

        # Only active manifests by default
        sql += " AND (is_active IS NULL OR is_active = 1)"

        sql += " ORDER BY min_open_time ASC"

        cur = conn.execute(sql, params)
        rows = cur.fetchall()

        manifests: list[CandleManifest] = []
        for row in rows:
            manifests.append(
                CandleManifest(
                    manifest_id=row[0],
                    exchange=row[1],
                    market_type=row[2],
                    symbol=row[3],
                    price_type=row[4],
                    timeframe=row[5],
                    partition_path=row[6],
                    min_open_time=row[7],
                    max_open_time=row[8],
                    row_count=row[9],
                    schema_hash=row[10],
                    checksum=row[11],
                    file_size_bytes=row[12],
                    provider=row[13],
                    ingested_at=row[14],
                    created_at=row[15],
                    updated_at=row[16],
                )
            )

        return manifests

    def detect_gaps(self, query: BarQuery) -> list[DataGap]:
        """Detect gaps in the candle data for the given query.

        Args:
            query: BarQuery specifying instrument, timeframe, and time range

        Returns:
            List of DataGap objects representing missing ranges
        """
        manifests = self.list_manifests(query)
        if not manifests:
            # If no manifests at all, the entire requested range is a gap
            exchange, market_type, symbol, price_type, timeframe, start_ms, end_ms = _storage_identity(query)
            return [
                DataGap(
                    gap_id=f"gap_{_storage_instrument_key(query)}_{timeframe}_{start_ms}_{end_ms}",
                    exchange=exchange,
                    market_type=market_type,
                    symbol=symbol,
                    price_type=price_type,
                    timeframe=timeframe,
                    provider=self.provider,
                    gap_start=start_ms,
                    gap_end=end_ms,
                    created_at=int(time.time() * 1000),
                    updated_at=int(time.time() * 1000),
                )
            ]

        gaps: list[DataGap] = []
        exchange, market_type, symbol, price_type, timeframe, _, _ = _storage_identity(query)

        for i in range(len(manifests) - 1):
            current = manifests[i]
            next_m = manifests[i + 1]
            # Check if there's a gap between current max and next min
            if next_m.min_open_time - current.max_open_time > 60_000:  # > 1 minute gap
                gaps.append(
                    DataGap(
                        gap_id=f"gap_{_storage_instrument_key(query)}_{timeframe}_{current.max_open_time}_{next_m.min_open_time}",
                        exchange=exchange,
                        market_type=market_type,
                        symbol=symbol,
                        price_type=price_type,
                        timeframe=timeframe,
                        provider=self.provider,
                        gap_start=current.max_open_time,
                        gap_end=next_m.min_open_time,
                        severity="minor",
                        created_at=int(time.time() * 1000),
                        updated_at=int(time.time() * 1000),
                    )
                )

        return gaps


__all__ = ["CandleStorage"]
