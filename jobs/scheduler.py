"""JobScheduler — section 7.6: job scheduling with locking/retry/dedupe."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from openpine.jobs.models import Job, JobStatus, JobType


@dataclass
class JobScheduler:
    """In-memory job scheduler with locking, deduplication, and serialization.

    Section 7.6 / 33.5 contracts:
    - enqueue with same idempotency_key returns existing job (dedupe)
    - serialization_key prevents concurrent processing of same strategy
    - Parallelism ONLY across different strategy_id values

    Thread-safe using a reentrant lock.
    """

    _queue: list[Job] = field(default_factory=list)
    _jobs: dict[str, Job] = field(default_factory=dict)
    _running: dict[str, str] = field(default_factory=dict)  # serialization_key -> job_id
    _locks: dict[str, tuple[str, int]] = field(default_factory=dict)  # resource -> (owner, expiry_ms)
    _idempotency_map: dict[str, str] = field(default_factory=dict)  # idempotency_key -> job_id

    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ── enqueue ─────────────────────────────────────────────────────────────────

    def enqueue(self, job: Job) -> Job:
        """Enqueue job. Rejects if idempotency_key already exists (dedupe).

        If a job with the same idempotency_key is already in the system
        (pending, running, or terminal), returns the existing job instead.
        """
        with self._lock:
            if job.idempotency_key:
                existing_id = self._idempotency_map.get(job.idempotency_key)
                if existing_id is not None:
                    existing = self._jobs.get(existing_id)
                    if existing is not None:
                        return existing

            job.id = job.id or str(uuid.uuid4())
            self._jobs[job.id] = job
            if job.idempotency_key:
                self._idempotency_map[job.idempotency_key] = job.id
            self._queue.append(job)
            self._requeue_sort()
            return job

    def _requeue_sort(self) -> None:
        """Re-sort pending queue by priority descending, then created_at ascending."""
        self._queue.sort(key=lambda j: (-j.priority, j.created_at))

    # ── dequeue ────────────────────────────────────────────────────────────────

    def dequeue(self) -> Job | None:
        """Dequeue highest-priority pending job.

        Respects serialization_key: skips jobs whose serialization_key
        is already in the _running map (another job with the same
        strategy_id is still running).
        """
        with self._lock:
            for i, job in enumerate(self._queue):
                if job.status != JobStatus.PENDING:
                    continue
                if job.serialization_key and job.serialization_key in self._running:
                    continue
                self._queue.pop(i)
                return job
            return None

    def mark_running(self, job_id: str) -> None:
        """Mark job as RUNNING. Records started_at and registers serialization_key."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.RUNNING
            job.started_at = int(time.time() * 1000)
            job.touch()
            if job.serialization_key:
                self._running[job.serialization_key] = job_id

    def mark_done(self, job_id: str, result: dict | None = None) -> None:
        """Mark job as DONE with optional result."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.DONE
            job.finished_at = int(time.time() * 1000)
            job.result = result
            job.touch()
            if job.serialization_key and self._running.get(job.serialization_key) == job_id:
                del self._running[job.serialization_key]

    def mark_failed(self, job_id: str, error: str) -> None:
        """Mark job as FAILED with error message."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.FAILED
            job.finished_at = int(time.time() * 1000)
            job.error = error
            job.touch()
            if job.serialization_key and self._running.get(job.serialization_key) == job_id:
                del self._running[job.serialization_key]

    def cancel(self, job_id: str) -> None:
        """Cancel a pending or running job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
                return
            job.status = JobStatus.CANCELLED
            job.finished_at = int(time.time() * 1000)
            job.touch()
            if job.serialization_key and self._running.get(job.serialization_key) == job_id:
                del self._running[job.serialization_key]

    # ── query ─────────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Job | None:
        """Return job by id or None."""
        return self._jobs.get(job_id)

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        """List all jobs, optionally filtered by status."""
        jobs = list(self._jobs.values())
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        jobs.sort(key=lambda j: -j.created_at)
        return jobs

    # ── distributed locking ───────────────────────────────────────────────────

    def acquire_lock(self, resource: str, owner: str, ttl_seconds: int = 60) -> bool:
        """Acquire a TTL-based lock on a resource.

        Returns True if lock was acquired (or already held by same owner).
        Returns False if resource is locked by another owner.
        """
        with self._lock:
            entry = self._locks.get(resource)
            now_ms = int(time.time() * 1000)
            ttl_ms = ttl_seconds * 1000
            if entry is not None:
                lock_owner, expiry_ms = entry
                if lock_owner == owner:
                    # Refresh
                    self._locks[resource] = (owner, now_ms + ttl_ms)
                    return True
                if expiry_ms > now_ms:
                    return False  # Stale but not expired yet
                # Expired — allow re-acquisition below
            self._locks[resource] = (owner, now_ms + ttl_ms)
            return True

    def release_lock(self, resource: str, owner: str) -> None:
        """Release a lock only if owned by the given owner."""
        with self._lock:
            entry = self._locks.get(resource)
            if entry is not None and entry[0] == owner:
                del self._locks[resource]

    def recover_stale_locks(self) -> int:
        """Remove expired locks. Returns count of recovered locks."""
        with self._lock:
            now_ms = int(time.time() * 1000)
            stale = [
                resource
                for resource, (_, expiry_ms) in self._locks.items()
                if expiry_ms <= now_ms
            ]
            for resource in stale:
                del self._locks[resource]
            return len(stale)

    # ── serialization helpers ─────────────────────────────────────────────────

    def is_running(self, serialization_key: str) -> bool:
        """Return True if a job with this serialization_key is currently running."""
        return serialization_key in self._running
