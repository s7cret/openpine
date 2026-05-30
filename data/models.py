"""Data models for candle data lake.

Section OP-DL-004 of OpenPine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


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
    file_size_bytes: Optional[int] = None
    provider: str = "binance"
    ingested_at: int = 0
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class DataRequirement:
    """Data requirement for a strategy.

    Represents a need to load/store OHLCV bars.
    """

    requirement_id: str
    exchange: str
    market_type: str
    symbol: str
    price_type: str
    timeframe: str
    provider: str
    from_time: Optional[int] = None
    to_time: Optional[int] = None
    reason: Optional[str] = None
    required_by_strategy_ids: str = ""
    status: str = "pending"
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class AggregationRequirement:
    """Aggregation requirement — what aggregations are needed.

    Represents a need to aggregate from lower timeframe to higher timeframe.
    """

    requirement_id: str
    instrument_key: str
    source_timeframe: str
    target_timeframe: str
    from_time: Optional[int] = None
    to_time: Optional[int] = None
    required_by_strategy_ids: str = ""
    status: str = "pending"
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
    filled_by_job_id: Optional[str] = None
    filled_at: Optional[int] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class WriteResult:
    """Result of a candle write operation."""

    success: bool
    rows_written: int = 0
    partition_path: Optional[str] = None
    error: Optional[str] = None
    manifests_created: list[CandleManifest] = field(default_factory=list)


@dataclass
class EnsureDataResult:
    """Result of ensure_data operation."""

    success: bool
    gaps_filled: int = 0
    gaps_remaining: list[DataGap] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class CandleCommitResult:
    """Result of on_candle_closed operation."""

    success: bool
    manifest_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DataPlan:
    """Data plan containing requirements for the active universe."""

    requirements: list[DataRequirement] = field(default_factory=list)
    aggregation_requirements: list[AggregationRequirement] = field(default_factory=list)


@dataclass
class FeaturePlan:
    """Feature plan containing feature requirements."""

    feature_requirements: list = field(default_factory=list)  # list[FeatureRequirement]


__all__ = [
    "CandleManifest",
    "DataRequirement",
    "AggregationRequirement",
    "DataGap",
    "WriteResult",
    "EnsureDataResult",
    "CandleCommitResult",
    "DataPlan",
    "FeaturePlan",
]
