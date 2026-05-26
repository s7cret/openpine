"""OpenPine domain contracts — single source of truth for shared types."""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

import pydantic


class Status(str, Enum):
    """Strategy/system status enum."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"
    DONE = "done"


class RuntimeStatus(str, Enum):
    """Runtime execution status."""

    IDLE = "idle"
    BACKTEST = "backtest"
    REPLAY = "replay"
    PAPER = "paper"
    LIVE = "live"


class InstrumentKey(pydantic.BaseModel):
    """Unique instrument identifier — section 5.4."""

    symbol: str
    exchange: str = "BINANCE"
    base: Optional[str] = None
    quote: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.exchange}:{self.symbol}"


class Timeframe(pydantic.BaseModel):
    """Timeframe representation."""

    value: str  # e.g. "1m", "5m", "1h", "1d"

    @property
    def minutes(self) -> int:
        """Convert to minutes."""
        mapping = {
            "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
            "1d": 1440, "1w": 10080,
        }
        return mapping.get(self.value, 0)


class Bar(pydantic.BaseModel):
    """Single OHLCV bar — section 5.4."""

    model_config = pydantic.ConfigDict(frozen=True)

    instrument_key: InstrumentKey
    timeframe: Timeframe
    timestamp: int  # milliseconds since epoch
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True

    @property
    def open_time_ms(self) -> int:
        return self.timestamp

    @property
    def close_time_ms(self) -> int:
        return self.timestamp + (self.timeframe.minutes * 60 * 1000)


class BarQuery(pydantic.BaseModel):
    """Bar query contract — section 30.2, single read contract."""

    instrument_key: InstrumentKey
    timeframe: Timeframe
    start_ms: int
    end_ms: int
    limit: Optional[int] = None

    def __str__(self) -> str:
        return (
            f"BarQuery({self.instrument_key} {self.timeframe.value} "
            f"{self.start_ms}-{self.end_ms})"
        )


class DataRequirement(pydantic.BaseModel):
    """Data requirement for strategy — section 5.7."""

    instrument_key: InstrumentKey
    timeframe: Timeframe
    start_ms: int
    end_ms: int


class StrategyInstance(pydantic.BaseModel):
    """Strategy instance — section 5.3."""

    id: str
    artifact_id: str
    params_hash: str
    instrument_key: InstrumentKey
    timeframe: Timeframe
    status: Status = Status.PENDING
    created_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))


class PineSource(pydantic.BaseModel):
    """Pine source — section 5.1."""

    id: str
    name: str
    source_text: str
    version: str = "1.0.0"
    created_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))


class CompileArtifact(pydantic.BaseModel):
    """Compile artifact — section 5.2."""

    id: str
    source_id: str
    params_hash: str
    artifact_path: str
    compile_meta: dict
    created_at: int = pydantic.Field(default_factory=lambda: int(time.time() * 1000))


class StrategyRuntimeError(pydantic.BaseModel):
    """Strategy runtime error event — section 33.6."""

    strategy_id: str
    artifact_id: str
    params_hash: str
    instrument_key: InstrumentKey
    timeframe: Timeframe
    bar_time: int
    error_type: str
    message: str
    traceback_id: str
    job_id: Optional[str] = None
    strategy_status_after: Status = Status.ERROR
