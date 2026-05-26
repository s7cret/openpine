"""Worker pools — section 7.7 and 33.8."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum

from openpine.jobs.models import Job, JobStatus, JobType
from openpine.jobs.scheduler import JobScheduler


class WorkerStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class WorkerState:
    """Snapshot of a single worker's state."""

    worker_id: str
    job_id: str | None
    last_heartbeat: int
    status: str


class WorkerPool:
    """Generic worker pool with heartbeat tracking.

    Section 7.7: manages a pool of workers that lease jobs from the scheduler.
    """

    def __init__(self, scheduler: JobScheduler, max_workers: int = 4):
        self.scheduler = scheduler
        self.max_workers = max_workers
        self._workers: dict[str, WorkerState] = {}
        self._heartbeats: dict[str, int] = {}  # worker_id -> last heartbeat ms
        self._running = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the worker pool."""
        with self._lock:
            self._running = True
            self._stop_event.clear()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the worker pool gracefully."""
        with self._lock:
            self._running = False
        self._stop_event.set()

    def worker_heartbeat(self, worker_id: str) -> None:
        """Record a heartbeat from a worker."""
        with self._lock:
            self._heartbeats[worker_id] = int(time.time() * 1000)
            if worker_id in self._workers:
                self._workers[worker_id].last_heartbeat = int(time.time() * 1000)

    def register_worker(self, worker_id: str) -> None:
        """Register a new worker."""
        with self._lock:
            self._workers[worker_id] = WorkerState(
                worker_id=worker_id,
                job_id=None,
                last_heartbeat=int(time.time() * 1000),
                status=WorkerStatus.IDLE.value,
            )
            self._heartbeats[worker_id] = int(time.time() * 1000)

    def get_status(self) -> dict:
        """Return a status dict for the pool."""
        with self._lock:
            return {
                "running": self._running,
                "max_workers": self.max_workers,
                "active_workers": len(self._workers),
                "heartbeats": dict(self._heartbeats),
            }


class AggregationWorkerPool(WorkerPool):
    """Section 33.8: builds OHLCV target timeframe candles ONLY.

    This pool is dedicated to aggregation jobs — computing higher-timeframe
    candles from lower-timeframe source candles.
    """

    JOB_TYPES = {JobType.BACKFILL}

    def __init__(self, scheduler: JobScheduler, max_workers: int = 2):
        super().__init__(scheduler, max_workers=max_workers)


class FeatureWorkerPool(WorkerPool):
    """Section 33.8: computes indicators/features ONLY after candles ready.

    This pool is dedicated to feature jobs — computing indicators that depend
    on pre-computed candles.
    """

    JOB_TYPES = {JobType.BACKTEST, JobType.OPTIMIZER}

    def __init__(self, scheduler: JobScheduler, max_workers: int = 2):
        super().__init__(scheduler, max_workers=max_workers)
