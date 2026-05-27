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
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from openpine.data.bar_query import BarQuery
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


class CandleStorage:
    """CandleStorage manages OHLCV parquet partitions with SQLite manifest tracking.

    Layout:
        ~/.openpine/data/candles/
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
        data_root: str = "~/.openpine/data",
        sqlite_path: str = "~/.openpine/openpine.sqlite",
        provider: str = "binance",
    ) -> None:
        """Initialize CandleStorage.

        Args:
            data_root: Root directory for parquet candle data
            sqlite_path: Path to SQLite database
            provider: Default provider name
        """
        self.data_root = Path(os.path.expanduser(data_root))
        self.sqlite_path = Path(os.path.expanduser(sqlite_path))
        self.provider = provider
        self._conn: Optional[object] = None  # sqlite3.Connection set lazily
        self._schema_hash = _compute_schema_hash()

    @property
    def candles_root(self) -> Path:
        """Root directory for candle parquet files."""
        return self.data_root / "candles"

    def _get_conn(self) -> "sqlite3.Connection":
        """Lazy-open SQLite connection."""
        import sqlite3

        if self._conn is None:
            self._conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
            self._conn.execute("PRAGMA busy_timeout=30000")
        return self._conn  # type: ignore[return-value]

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
        from datetime import datetime

        dt = datetime.utcfromtimestamp(open_time_ms / 1000)
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

        # Resolve instrument parts
        if instrument_key:
            parts = instrument_key.split(":")
            if len(parts) != 4:
                return WriteResult(success=False, error=f"Invalid instrument_key format: {instrument_key}")
            exchange, market_type, symbol, price_type = parts
        else:
            # Try to get from first bar
            first = candles[0]
            if not hasattr(first, "instrument_key"):
                return WriteResult(
                    success=False,
                    error="instrument_key is required either as parameter or bar attribute"
                )
            parts = first.instrument_key.split(":")
            if len(parts) != 4:
                return WriteResult(success=False, error=f"Invalid instrument_key: {first.instrument_key}")
            exchange, market_type, symbol, price_type = parts

        # Group candles by year/month partition
        # All bars within the same year/month go into the same parquet file
        from datetime import datetime

        partitions: dict[tuple, list] = {}
        for bar in candles:
            open_time_ms = bar.time
            dt = datetime.utcfromtimestamp(open_time_ms / 1000)
            bucket = (dt.year, dt.month)
            key = (exchange, market_type, symbol, price_type, timeframe, bucket)
            if key not in partitions:
                partitions[key] = []
            partitions[key].append(bar)

        manifests: list[CandleManifest] = []
        total_rows = 0

        for (exchange, market_type, symbol, price_type, timeframe, (year, month)), bars in partitions.items():
            first_bar = bars[0]
            # Use first bar's time to determine partition path
            partition_dir = self._partition_path(
                exchange, market_type, symbol, price_type, timeframe, first_bar.time
            )

            # Determine filename based on mode
            if mode == WriteMode.REPLACE_PARTITION:
                filename = f"part-{int(time.time() * 1000):012d}.parquet"
            else:
                # Generate unique filename
                ts = int(time.time() * 1000)
                filename = f"part-{ts}.parquet"

            final_path = partition_dir / filename
            tmp_path = self._tmp_path(final_path)

            # Build DataFrame
            rows = []
            for bar in bars:
                rows.append(
                    {
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
                        "provider": self.provider,
                        "ingested_at": int(time.time() * 1000),
                    }
                )

            df = pd.DataFrame(rows)
            df = df.sort_values("open_time")

            # Validate schema
            table = pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False)

            # Write to .tmp
            partition_dir.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, str(tmp_path), compression="zstd")

            # Atomic rename
            shutil.move(str(tmp_path), str(final_path))

            # Compute metadata
            checksum = _compute_checksum(df)
            file_size = final_path.stat().st_size
            min_time = int(df["open_time"].min())
            max_time = int(df["open_time"].max())
            row_count = len(df)

            # Create manifest
            manifest = CandleManifest(
                manifest_id=f"m_{int(time.time() * 1000)}_{hashlib.sha256(final_path.name.encode()).hexdigest()[:8]}",
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                price_type=price_type,
                timeframe=timeframe,
                partition_path=str(final_path),
                min_open_time=min_time,
                max_open_time=max_time,
                row_count=row_count,
                schema_hash=self._schema_hash,
                checksum=checksum,
                file_size_bytes=file_size,
                provider=self.provider,
                ingested_at=int(time.time() * 1000),
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
            )

            # Insert manifest to SQLite
            self._insert_manifest(manifest)
            manifests.append(manifest)
            total_rows += row_count

        return WriteResult(
            success=True,
            rows_written=total_rows,
            partition_path=str(final_path) if manifests else None,
            manifests_created=manifests,
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
        from marketdata_provider.core.bar import Bar

        exchange, market_type, symbol, price_type = query.instrument_parts

        # Find matching manifest files
        manifests = self.list_manifests(query)
        if not manifests:
            return []

        all_rows: list[dict] = []
        for m in manifests:
            pq_path = Path(m.partition_path)
            if not pq_path.exists():
                continue
            pf = pq.ParquetFile(str(pq_path))
            table = pf.read()
            df = table.to_pandas()

            # Apply time filter
            if query.from_time is not None:
                df = df[df["open_time"] >= query.from_time]
            if query.to_time is not None:
                df = df[df["open_time"] <= query.to_time]

            all_rows.extend(df.to_dict("records"))

        if not all_rows:
            return []

        # Sort by open_time
        all_rows.sort(key=lambda r: r["open_time"])

        # Apply limit
        if query.limit is not None:
            all_rows = all_rows[-query.limit :]

        # Convert to Bar objects
        bars = []
        for row in all_rows:
            bar = Bar(
                time=int(row["open_time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if row["volume"] else 0.0,
                time_close=int(row["close_time"]) if row["close_time"] else int(row["open_time"]),
            )
            bars.append(bar)

        return bars

    def list_manifests(self, query: BarQuery) -> list[CandleManifest]:
        """List candle manifests matching the query.

        Args:
            query: BarQuery specifying instrument and time range

        Returns:
            List of CandleManifest objects
        """
        import sqlite3

        exchange, market_type, symbol, price_type = query.instrument_parts

        conn = self._get_conn()
        sql = """
            SELECT manifest_id, exchange, market_type, symbol, price_type, timeframe,
                   partition_path, min_open_time, max_open_time, row_count, schema_hash,
                   checksum, file_size_bytes, provider, ingested_at, created_at, updated_at
            FROM candle_manifests
            WHERE exchange = ? AND market_type = ? AND symbol = ? AND price_type = ?
              AND timeframe = ?
        """
        params: list = [exchange, market_type, symbol, price_type, query.timeframe]

        if query.from_time is not None:
            sql += " AND max_open_time >= ?"
            params.append(query.from_time)
        if query.to_time is not None:
            sql += " AND min_open_time <= ?"
            params.append(query.to_time)

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
            if query.from_time and query.to_time:
                return [
                    DataGap(
                        gap_id=f"gap_{query.instrument_key}_{query.timeframe}_{query.from_time}_{query.to_time}",
                        exchange=query.instrument_parts[0],
                        market_type=query.instrument_parts[1],
                        symbol=query.instrument_parts[2],
                        price_type=query.instrument_parts[3],
                        timeframe=query.timeframe,
                        provider=self.provider,
                        gap_start=query.from_time,
                        gap_end=query.to_time,
                        created_at=int(time.time() * 1000),
                        updated_at=int(time.time() * 1000),
                    )
                ]
            return []

        gaps: list[DataGap] = []
        from_time = query.from_time or manifests[0].min_open_time
        to_time = query.to_time or manifests[-1].max_open_time

        for i in range(len(manifests) - 1):
            current = manifests[i]
            next_m = manifests[i + 1]
            # Check if there's a gap between current max and next min
            if next_m.min_open_time - current.max_open_time > 60_000:  # > 1 minute gap
                gaps.append(
                    DataGap(
                        gap_id=f"gap_{query.instrument_key}_{query.timeframe}_{current.max_open_time}_{next_m.min_open_time}",
                        exchange=query.instrument_parts[0],
                        market_type=query.instrument_parts[1],
                        symbol=query.instrument_parts[2],
                        price_type=query.instrument_parts[3],
                        timeframe=query.timeframe,
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
