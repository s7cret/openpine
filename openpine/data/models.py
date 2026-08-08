"""Data models for candle data lake.

Section OP-DL-004 of OpenPine.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CandleManifest:
    """Manifest entry for a candle parquet partition."""

    manifest_id: str
    exchange: str
    market_type: str
    symbol: str
    price_type: str
    timeframe: str
    partition_path: str
    min_open_time: int
    max_open_time: int
    row_count: int
    schema_hash: str
    checksum: str
    file_size_bytes: int | None = None
    provider: str = "binance"
    ingested_at: int = 0
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class DataGap:
    """Gap in the data — missing bars for an instrument/timeframe/range."""

    gap_id: str
    exchange: str
    market_type: str
    symbol: str
    price_type: str
    timeframe: str
    provider: str
    gap_start: int
    gap_end: int
    severity: str = "minor"
    status: str = "open"
    filled_by_job_id: str | None = None
    filled_at: int | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class WriteResult:
    """Result of a candle write operation."""

    success: bool
    rows_written: int = 0
    partition_path: str | None = None
    error: str | None = None
    manifests_created: list[CandleManifest] = field(default_factory=list)


@dataclass
class CandleCommitResult:
    """Result of on_candle_closed operation."""

    success: bool
    manifest_id: str | None = None
    error: str | None = None


__all__ = [
    "CandleCommitResult",
    "CandleManifest",
    "DataGap",
    "WriteResult",
]
