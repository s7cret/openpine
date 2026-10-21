from dataclasses import dataclass

from .model import HistoricalData


@dataclass(frozen=True)
class DataQuery:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class MarketSnapshot:
    data: HistoricalData
    # Provenance is stored as canonical immutable JSON bytes.
    provenance: bytes
    sha256: str
