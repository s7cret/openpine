"""Versioned storage identities and process resource policy. No database imports."""

import math
import re
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


@dataclass(frozen=True)
class RunPlan:
    input_ref: str
    snapshot_id: str
    timeout_seconds: float
    execution_sha256: str
    spec_lock_sha256: str
    profile_id: str = "openpine.historical-bars.1"
    threads: int = 1
    memory_mb: int = 512
    max_attempts: int = 1
    heartbeat_timeout: float = 15.0
    output_schema: str = "openpine.run-summary.3"
    schema_version: str = "openpine.run-plan.1"

    def __post_init__(self):
        if (any(type(x) is not str or not re.fullmatch(r"[0-9a-f]{64}", x)
                for x in (self.input_ref, self.snapshot_id, self.execution_sha256, self.spec_lock_sha256))
                or type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 3600
                or type(self.heartbeat_timeout) not in (int, float) or not 2 <= self.heartbeat_timeout <= 120
                or not math.isfinite(self.heartbeat_timeout)
                or any(type(x) is not int for x in (self.threads, self.memory_mb, self.max_attempts))
                or not 1 <= self.threads <= 8 or not 128 <= self.memory_mb <= 1_048_576
                or not 1 <= self.max_attempts <= 3 or self.profile_id != "openpine.historical-bars.1"
                or self.output_schema != "openpine.run-summary.3" or self.schema_version != "openpine.run-plan.1"):
            raise ValueError("Invalid immutable run plan.")


@dataclass(frozen=True)
class CoordinatorPolicy:
    memory_mb: int = 512
    base_mb: int = 128
    preparation_mb: int = 256
    read_mb: int = 128
    preparations: int = 1
    readers: int = 2
    connections: int = 8

    def __post_init__(self):
        if (any(type(v) is not int or v < 1 for v in self.__dict__.values())
                or self.base_mb + max(self.preparation_mb, self.read_mb) > self.memory_mb
                or self.connections > 64 or self.preparations > 8 or self.readers > 16):
            raise ValueError("Invalid coordinator resource policy.")


class BarSource(Protocol):
    identity: DatasetIdentity

    def __iter__(self): ...


class OutputSink(Protocol):
    def emit(self, output_id, title, value_type, value, index, timestamp): ...


class JobRepository(Protocol):
    def get(self, job_id): ...
    def list(self, limit=100): ...
    def active(self): ...
    def insert(self, state, request, *, imported_from=None, plan=None): ...
    def plan(self, job_id): ...
    def attach_plan(self, job_id, plan): ...
    def request(self, job_id): ...
    def claim(self, state): ...
    def save(self, state): ...
    def finish(self, state): ...
    def retry(self, state, previous_attempt, error): ...
    def progress(self, state): ...
    def scanned(self, name): ...
    def record_import(self, path, job_id=None, error=None): ...
