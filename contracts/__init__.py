"""OpenPine domain contracts — single source of truth for shared types."""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

import pydantic
from marketdata_provider.contracts import Bar, InstrumentKey, Timeframe


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
