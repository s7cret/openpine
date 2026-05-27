"""Contracts: enums and constants for candle data lake.

Section OP-DL-004 of OpenPine.
"""

from __future__ import annotations

from enum import Enum


class WriteMode(str, Enum):
    """Atomic write mode for candle parquet files."""

    APPEND_ONLY = "append_only"
    UPSERT_PARTITION = "upsert_partition"
    REPAIR_RANGE = "repair_range"
    REPLACE_PARTITION = "replace_partition"


# Parquet partition layout template
PARQUET_LAYOUT = (
    "exchange={exchange}/"
    "market_type={market_type}/"
    "symbol={symbol}/"
    "price_type={price_type}/"
    "timeframe={timeframe}/"
    "year={year}/"
    "month={month:02d}/"
)

# Data directory root
DATA_ROOT = "~/.openpine/data"

# Parquet schema fields
PARQUET_FIELDS = [
    "exchange",
    "market_type",
    "symbol",
    "price_type",
    "timeframe",
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades_count",
    "is_closed",
    "source",
    "provider",
    "ingested_at",
]

__all__ = [
    "WriteMode",
    "PARQUET_LAYOUT",
    "DATA_ROOT",
    "PARQUET_FIELDS",
]
