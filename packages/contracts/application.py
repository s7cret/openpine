"""Application requests and job lifecycle shared by CLI, worker and HTTP."""

from dataclasses import dataclass
from enum import StrEnum

from .model import HISTORICAL_PROFILE_ID, HistoricalData


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED,
                               JobStatus.CANCELLED, JobStatus.TIMED_OUT})


@dataclass(frozen=True)
class CompileRequest:
    source: str
    source_name: str = "<source>"
    profile_id: str = HISTORICAL_PROFILE_ID


@dataclass(frozen=True)
class RunRequest:
    compilation: CompileRequest
    data: HistoricalData | None = None
