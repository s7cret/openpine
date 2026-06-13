"""Job domain models for OpenPine JobScheduler."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class JobType(StrEnum):
    BACKTEST = "backtest"
    OPTIMIZER = "optimizer"
    BACKFILL = "backfill"
    LIVE_BAR_PROCESS = "live_bar_process"
    PAPER_BAR_PROCESS = "paper_bar_process"
    OBSERVE_BAR_PROCESS = "observe_bar_process"
    REPORT = "report"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """A schedulable job unit.

    Section 7.6 / 33.5 contracts:
    - idempotency_key prevents duplicate bar execution
    - serialization_key=strategy_id prevents concurrent processing
      of two bars for the same strategy
    """

    # Required fields first; all fields with defaults come after.
    job_type: JobType
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str | None = None
    status: JobStatus = JobStatus.PENDING
    idempotency_key: str | None = None
    serialization_key: str | None = None  # strategy_id for live/paper
    priority: int = 0
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    scheduled_at: int | None = None
    started_at: int | None = None
    finished_at: int | None = None
    error: str | None = None
    input: dict | None = None
    result: dict | None = None
    worker_id: str | None = None
    attempt: int = 1
    max_retries: int = 3

    def touch(self) -> None:
        """Update updated_at timestamp."""
        self.updated_at = int(time.time() * 1000)
