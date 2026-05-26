"""Event types for OpenPine EventBus.

Section 18 + 33.6 of OpenPine TZ v3.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """Required event types — section 18.1."""

    CANDLE_CLOSED = "candle_closed"
    AGGREGATE_CLOSED = "aggregate_closed"
    STRATEGY_RUNTIME_ERROR = "strategy_runtime_error"
    JOB_STARTED = "job_started"
    JOB_DONE = "job_done"
    JOB_FAILED = "job_failed"


@dataclass
class Event:
    """Event envelope — section 18.2.

    All events are durable (persisted to SQLite) by default.
    Non-durable events are used for in-process notifications only.
    """

    event_id: str
    event_type: EventType
    payload: dict
    timestamp_ms: int
    durable: bool = True

    @classmethod
    def create(
        cls,
        event_type: EventType,
        payload: dict,
        durable: bool = True,
    ) -> "Event":
        """Factory: create a new event with generated ID and current timestamp."""
        return cls(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            payload=payload,
            timestamp_ms=int(time.time() * 1000),
            durable=durable,
        )


@dataclass
class CandleClosedEventPayload:
    """Payload for CandleClosed event — section 18.1."""

    instrument_key: dict  # serialized InstrumentKey
    timeframe: dict  # serialized Timeframe
    bar_timestamp: int
    bar: dict  # serialized Bar


@dataclass
class StrategyRuntimeErrorPayload:
    """Payload for StrategyRuntimeError event — section 33.6.

    Required durable event with all 11 fields:
    strategy_id, artifact_id, params_hash, instrument_key, timeframe,
    bar_time, error_type, message, traceback_id, job_id, strategy_status_after
    """

    strategy_id: str
    artifact_id: str
    params_hash: str
    instrument_key: dict  # serialized InstrumentKey
    timeframe: dict  # serialized Timeframe
    bar_time: int
    error_type: str
    message: str
    traceback_id: str
    job_id: str | None
    strategy_status_after: str  # e.g. "error"
