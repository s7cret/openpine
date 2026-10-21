"""Versioned storage identities and process resource policy. No database imports."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DatasetIdentity:
    sha256: str
    context_id: str
    symbol: str
    timeframe: str
    count: int
    first_ms: int
    last_ms: int
    hash_scheme: str = "openpine.candle-blocks.1"


@dataclass(frozen=True)
class ResourcePolicy:
    workers: int = 2
    threads_per_worker: int = 1
    memory_mb_per_worker: int = 512
    total_memory_mb: int = 1024
    max_attempts: int = 1
    heartbeat_timeout: float = 15.0

    def __post_init__(self):
        if (any(type(v) is not int for v in (self.workers, self.threads_per_worker,
                self.memory_mb_per_worker, self.total_memory_mb, self.max_attempts))
                or not 1 <= self.workers <= 32 or not 1 <= self.threads_per_worker <= 8
                or not 128 <= self.memory_mb_per_worker <= self.total_memory_mb
                or self.workers * self.memory_mb_per_worker > self.total_memory_mb
                or not 1 <= self.max_attempts <= 3 or not 2 <= self.heartbeat_timeout <= 120):
            raise ValueError("Invalid aggregate worker resource policy.")


class BarSource(Protocol):
    identity: DatasetIdentity

    def __iter__(self): ...


class OutputSink(Protocol):
    def emit(self, output_id, title, value_type, value, index, timestamp): ...


class JobRepository(Protocol):
    def get(self, job_id): ...
    def list(self, limit=100): ...
    def active(self): ...
    def insert(self, state, request, *, imported_from=None): ...
    def request(self, job_id): ...
    def claim(self, state): ...
    def save(self, state): ...
    def finish(self, state): ...
    def retry(self, state, previous_attempt, error): ...
    def progress(self, state): ...
    def scanned(self, name): ...
    def record_import(self, path, job_id=None, error=None): ...
