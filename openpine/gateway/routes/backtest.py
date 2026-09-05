"""Backtest routes — run, progress, results."""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing as mp
import os
import queue
import select
import signal
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from openpine._compat import structlog
from openpine.admission import admit_strategy_semantic_profile
from openpine.exchange_metadata import (
    default_price_tick,
    default_qty_rounding_mode,
    default_qty_step,
)
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.side_effects import persist_gateway_job, require_http_admit
from openpine.gateway.schemas import (
    BacktestEstimateResponse,
    BacktestProgress,
    BacktestRunDetail,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestTradeResponse,
)
from openpine.gateway.ws_manager import ws_manager
from openpine.timezones import parse_timestamp_ms
from openpine_contracts import AdmitError

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


class _BacktestCancelled(RuntimeError):
    """Raised only after the owned compute process tree has stopped."""


class _BacktestSupervisorShutdown(BaseException):
    """Interrupt the supervisor so it can clean descendants before exit."""


@dataclass
class _BacktestWorker:
    process: BaseProcess
    out: object
    process_group: int | None
    start_time: int | None
    owned_pid: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    cleanup_proven: bool = False
    cleanup_complete: object | None = None


@dataclass(frozen=True)
class _ArtifactBacktestSpec:
    pine_id: str
    artifact_id: str
    symbol: str
    timeframe: str
    cache_dir: str
    exchange: str
    market: str
    prefetch_end_ms: int
    source: bytes
    data_snapshot_hash: str
    execution_context: dict[str, Any]
    admitted_manifest: dict[str, Any]
    generated_artifact: dict[str, Any]
    run_hash: str
    bar_envelopes: list[dict[str, Any]]
    protocol_artifact_dir: str
    htf_bars: list[dict[str, Any]] | None = None
    htf_timeframe: str | None = None


_ACTIVE_BACKTEST_WORKERS: dict[str, _BacktestWorker] = {}
_RETAINED_BACKTEST_WORKERS: dict[str, list[_BacktestWorker]] = {}
_ACTIVE_BACKTEST_WORKERS_LOCK = threading.Lock()
_STARTING_BACKTEST_RUNS: set[str] = set()
_TERMINAL_BACKTEST_RUNS: set[str] = set()
_TERMINAL_BACKTEST_OUTCOMES: dict[str, str] = {}
_BACKTEST_THREAD_CONTEXT = threading.local()
_BACKTEST_ISOLATION_TIMEOUT_SECONDS = 15.0


class _BacktestLease:
    def __init__(self, limiter: "_BacktestAdmissionLimiter") -> None:
        self._limiter = limiter
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._limiter.release()


class _BacktestAdmissionLimiter:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._semaphore = threading.BoundedSemaphore(capacity)

    def try_acquire(self) -> _BacktestLease | None:
        if not self._semaphore.acquire(blocking=False):
            return None
        return _BacktestLease(self)

    def release(self) -> None:
        self._semaphore.release()


_RETAINED_BACKTEST_LEASES: dict[str, _BacktestLease] = {}


_ADMISSION_LIMITERS_LOCK = threading.Lock()
_IDEMPOTENCY_LOCK = threading.Lock()
_IDEMPOTENCY_PENDING_TTL_MS = 5 * 60 * 1000
_IDEMPOTENCY_RESULT_TTL_MS = 7 * 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class _IdempotencyClaim:
    result_id: str | None
    claim_token: str | None


class _IdempotencyClaimSuperseded(RuntimeError):
    pass


def _backtest_request_hash(body: object) -> str:
    model_dump = getattr(body, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
    else:
        payload = vars(body)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _claim_backtest_idempotency_storage(
    state: GatewayState, idempotency_key: str, request_hash: str
) -> _IdempotencyClaim:
    with _IDEMPOTENCY_LOCK:
        now = int(time.time() * 1000)
        claim_token = uuid.uuid4().hex
        state.storage.execute(
            """
            DELETE FROM api_idempotency
            WHERE scope = ? AND result_id IS NULL AND updated_at < ?
            """,
            ("backtest.run", now - _IDEMPOTENCY_PENDING_TTL_MS),
        )
        state.storage.execute(
            """
            DELETE FROM api_idempotency
            WHERE scope = ? AND result_id IS NOT NULL AND updated_at < ?
            """,
            ("backtest.run", now - _IDEMPOTENCY_RESULT_TTL_MS),
        )
        cursor = state.storage.execute(
            """
            INSERT OR IGNORE INTO api_idempotency
                (scope, idempotency_key, request_hash, claim_token, result_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?)
            """,
            ("backtest.run", idempotency_key, request_hash, claim_token, now, now),
        )
        state.storage.commit()
        if cursor.rowcount == 1:
            return _IdempotencyClaim(result_id=None, claim_token=claim_token)
        row = state.storage.execute(
            """
            SELECT request_hash, result_id FROM api_idempotency
            WHERE scope = ? AND idempotency_key = ?
            """,
            ("backtest.run", idempotency_key),
        ).fetchone()
        if row is None:
            raise HTTPException(503, "Unable to resolve idempotent backtest request")
        if row[0] != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used with a different request")
        if not row[1]:
            raise HTTPException(409, "The idempotent backtest request is still being scheduled")
        return _IdempotencyClaim(result_id=str(row[1]), claim_token=None)


def _claim_backtest_idempotency(
    state: GatewayState, idempotency_key: str, request_hash: str
) -> _IdempotencyClaim:
    try:
        return _claim_backtest_idempotency_storage(
            state, idempotency_key, request_hash
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error(
            "backtest_idempotency_storage_unavailable",
            operation="claim",
            error_type=exc.__class__.__name__,
        )
        raise HTTPException(503, "Backtest idempotency storage is unavailable") from exc


def _complete_backtest_idempotency_storage(
    state: GatewayState,
    idempotency_key: str,
    request_hash: str,
    claim_token: str,
    run_id: str,
) -> None:
    with _IDEMPOTENCY_LOCK:
        cursor = state.storage.execute(
            """
            UPDATE api_idempotency SET result_id = ?, updated_at = ?
            WHERE scope = ? AND idempotency_key = ?
              AND request_hash = ? AND claim_token = ? AND result_id IS NULL
            """,
            (
                run_id,
                int(time.time() * 1000),
                "backtest.run",
                idempotency_key,
                request_hash,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            state.storage.rollback()
            raise _IdempotencyClaimSuperseded
        state.storage.commit()


def _complete_backtest_idempotency(
    state: GatewayState,
    idempotency_key: str,
    request_hash: str,
    claim_token: str,
    run_id: str,
) -> None:
    try:
        _complete_backtest_idempotency_storage(
            state, idempotency_key, request_hash, claim_token, run_id
        )
    except _IdempotencyClaimSuperseded as exc:
        raise HTTPException(409, "Idempotency claim expired or was superseded") from exc
    except Exception as exc:
        log.error(
            "backtest_idempotency_storage_unavailable",
            operation="complete",
            error_type=exc.__class__.__name__,
        )
        raise HTTPException(503, "Backtest idempotency storage is unavailable") from exc


def _release_backtest_idempotency_storage(
    state: GatewayState, idempotency_key: str, request_hash: str, claim_token: str
) -> None:
    with _IDEMPOTENCY_LOCK:
        state.storage.execute(
            """
            DELETE FROM api_idempotency
            WHERE scope = ? AND idempotency_key = ? AND request_hash = ?
              AND claim_token = ? AND result_id IS NULL
            """,
            ("backtest.run", idempotency_key, request_hash, claim_token),
        )
        state.storage.commit()


def _release_backtest_idempotency(
    state: GatewayState, idempotency_key: str, request_hash: str, claim_token: str
) -> None:
    try:
        _release_backtest_idempotency_storage(
            state, idempotency_key, request_hash, claim_token
        )
    except Exception as exc:
        log.error(
            "backtest_idempotency_storage_unavailable",
            operation="release",
            error_type=exc.__class__.__name__,
        )
        raise HTTPException(503, "Backtest idempotency storage is unavailable") from exc


def _backtest_admission_limiter(state: GatewayState) -> _BacktestAdmissionLimiter:
    raw_limit = os.getenv("OPENPINE_BACKTEST_MAX_CONCURRENCY", "2")
    try:
        limit = max(1, min(32, int(raw_limit)))
    except ValueError:
        limit = 2
    with _ADMISSION_LIMITERS_LOCK:
        limiter = getattr(state, "_backtest_admission_limiter", None)
        if limiter is None or limiter.capacity != limit:
            limiter = _BacktestAdmissionLimiter(limit)
            setattr(state, "_backtest_admission_limiter", limiter)
        return limiter


def _proc_identity(pid: int) -> tuple[str, int, int] | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as exc:
        raise RuntimeError(f"unreadable process identity: {pid}") from exc
    closing = text.rfind(")")
    if closing < 0:
        raise RuntimeError(f"malformed process identity: {pid}")
    fields = text[closing + 2 :].split()
    if len(fields) <= 19:
        raise RuntimeError(f"malformed process identity: {pid}")
    try:
        return fields[0], int(fields[2]), int(fields[19])
    except ValueError as exc:
        raise RuntimeError(f"malformed process identity: {pid}") from exc


def _enable_child_subreaper() -> None:
    """Keep escaped grandchildren owned by the backtest worker on Linux."""

    if sys.platform != "linux":
        return
    if not Path("/proc/self/stat").exists():
        raise RuntimeError("Linux procfs is required for backtest child ownership")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")
    except (AttributeError, OSError) as exc:
        raise RuntimeError("unable to establish backtest child ownership") from exc


def _descendant_process_identities(root_pid: int) -> dict[int, int]:
    rows: dict[int, tuple[int, int, str]] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(stat_path.parent.name)
            text = stat_path.read_text(encoding="utf-8")
            closing = text.rfind(")")
            fields = text[closing + 2 :].split()
            rows[pid] = (int(fields[1]), int(fields[19]), fields[0])
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, IndexError, ValueError) as exc:
            raise RuntimeError(f"incomplete process ownership snapshot: {stat_path}") from exc
    descendants: dict[int, int] = {}
    frontier = {root_pid}
    while frontier:
        children = {
            pid
            for pid, (parent_pid, _start_time, state) in rows.items()
            if parent_pid in frontier and state != "Z" and pid not in descendants
        }
        for pid in children:
            descendants[pid] = rows[pid][1]
        frontier = children
    return descendants


def _pidfd_open(pid: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    descriptor = int(libc.syscall(434, pid, 0))  # __NR_pidfd_open on Linux
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


def _pidfd_send_signal(descriptor: int, sig: signal.Signals) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = int(libc.syscall(424, descriptor, int(sig), 0, 0))
    if result < 0:
        error = ctypes.get_errno()
        if error == 3:  # ESRCH: the pinned process has already exited.
            return
        raise OSError(error, os.strerror(error))


def _pidfd_has_exited(descriptor: int) -> bool:
    poller = select.poll()
    poller.register(descriptor, select.POLLIN)
    return bool(poller.poll(0))


def _process_tasks_are_stopped(pid: int, start_time: int) -> bool:
    """Require a stable all-TID group-stop snapshot for one exact process."""

    identity = _proc_identity(pid)
    if identity is None:
        return False
    if identity[2] != start_time:
        raise RuntimeError(f"process identity changed while stopping: {pid}")
    task_root = Path(f"/proc/{pid}/task")
    for _attempt in range(2):
        before = tuple(sorted(task_root.glob("[0-9]*/stat"), key=str))
        if not before:
            if _proc_identity(pid) is None:
                return False
            raise RuntimeError(f"incomplete process task snapshot: {pid}")
        states: list[str] = []
        for stat_path in before:
            try:
                text = stat_path.read_text(encoding="utf-8")
            except (FileNotFoundError, ProcessLookupError):
                break
            except PermissionError as exc:
                raise RuntimeError(
                    f"incomplete process task snapshot: {stat_path}"
                ) from exc
            closing = text.rfind(")")
            fields = text[closing + 2 :].split() if closing >= 0 else []
            if not fields:
                raise RuntimeError(f"malformed process task snapshot: {stat_path}")
            states.append(fields[0])
        else:
            after = tuple(sorted(task_root.glob("[0-9]*/stat"), key=str))
            final_identity = _proc_identity(pid)
            if final_identity is None:
                return False
            if final_identity[2] != start_time:
                raise RuntimeError(f"process identity changed while stopping: {pid}")
            if before == after and all(state in {"T", "t"} for state in states):
                return True
        time.sleep(0.001)
    return False


def _terminate_current_process_descendants(timeout: float = 2.0) -> None:
    """Freeze, discover to a fixed point, then kill every owned descendant."""

    root_pid = os.getpid()
    if _proc_identity(root_pid) is None:
        raise RuntimeError("backtest supervisor procfs identity is unavailable")
    deadline = time.monotonic() + max(0.0, timeout)
    owned: dict[int, tuple[int, int]] = {}
    frozen: set[int] = set()
    stable_scans = 0
    try:
        while time.monotonic() < deadline:
            snapshot = _descendant_process_identities(root_pid)
            for pid, start_time in snapshot.items():
                existing = owned.get(pid)
                if existing is not None:
                    if existing[0] != start_time:
                        raise RuntimeError(f"owned process identity changed: {pid}")
                    continue

                descriptor: int | None = None
                waited_for_pidfd = False
                while descriptor is None:
                    try:
                        descriptor = _pidfd_open(pid)
                    except OSError:
                        identity = _proc_identity(pid)
                        if (
                            identity is None
                            or identity[0] == "Z"
                            or identity[2] != start_time
                        ):
                            break
                        # Never destroy the subreaper boundary while a live
                        # descendant is still unpinned.  A transient pidfd
                        # acquisition failure is retried; a persistent one
                        # deliberately keeps this supervisor alive and owning
                        # the family until pinning becomes possible or the
                        # exact process exits.
                        waited_for_pidfd = True
                        time.sleep(0.005)
                waited_for_validation = False
                while descriptor is not None:
                    try:
                        identity = _proc_identity(pid)
                    except BaseException:
                        waited_for_validation = True
                        time.sleep(0.005)
                        continue
                    if identity is None or identity[0] == "Z":
                        try:
                            exited = _pidfd_has_exited(descriptor)
                        except BaseException:
                            waited_for_validation = True
                            time.sleep(0.005)
                            continue
                        if exited:
                            os.close(descriptor)
                            descriptor = None
                            break
                        waited_for_validation = True
                        time.sleep(0.005)
                        continue
                    if identity[2] != start_time:
                        # The numeric PID was reused before pidfd_open.  This
                        # descriptor is not owned and must never be signalled.
                        os.close(descriptor)
                        descriptor = None
                        break
                    try:
                        ancestry = _descendant_process_identities(root_pid)
                    except BaseException:
                        waited_for_validation = True
                        time.sleep(0.005)
                        continue
                    if ancestry.get(pid) == start_time:
                        break
                    try:
                        exited = _pidfd_has_exited(descriptor)
                    except BaseException:
                        waited_for_validation = True
                        time.sleep(0.005)
                        continue
                    if exited:
                        os.close(descriptor)
                        descriptor = None
                        break
                    # Keep the unvalidated pidfd quarantined without signals
                    # until exact ancestry proof becomes available.
                    waited_for_validation = True
                    time.sleep(0.005)
                if descriptor is None:
                    continue
                if waited_for_pidfd or waited_for_validation:
                    deadline = max(
                        deadline,
                        time.monotonic() + max(0.1, timeout),
                    )
                owned[pid] = (start_time, descriptor)

                _pidfd_send_signal(descriptor, signal.SIGSTOP)
                while time.monotonic() < deadline:
                    if _pidfd_has_exited(descriptor):
                        os.close(descriptor)
                        owned.pop(pid, None)
                        break
                    if _process_tasks_are_stopped(pid, start_time):
                        stopped_identity = _proc_identity(pid)
                        stopped_ancestry = _descendant_process_identities(root_pid)
                        if (
                            stopped_identity is None
                            or stopped_identity[2] != start_time
                            or stopped_ancestry.get(pid) != start_time
                        ):
                            if _pidfd_has_exited(descriptor):
                                os.close(descriptor)
                                owned.pop(pid, None)
                                break
                            raise RuntimeError(
                                f"owned descendant changed while stopped: {pid}"
                            )
                        frozen.add(pid)
                        break
                    time.sleep(0.005)
                else:
                    raise RuntimeError(f"owned descendant did not stop: {pid}")

            for pid, (_start_time, descriptor) in tuple(owned.items()):
                if _pidfd_has_exited(descriptor):
                    os.close(descriptor)
                    owned.pop(pid, None)
                    frozen.discard(pid)

            after = _descendant_process_identities(root_pid)
            unseen = {
                pid
                for pid, start_time in after.items()
                if owned.get(pid, (None, -1))[0] != start_time
            }
            if not unseen and all(pid in frozen for pid in owned):
                stable_scans += 1
                if stable_scans >= 2:
                    break
            else:
                stable_scans = 0
            time.sleep(0.005)
        else:
            raise RuntimeError("backtest descendant discovery did not converge")

        for pid, (_start_time, descriptor) in owned.items():
            if pid not in frozen:
                raise RuntimeError(f"owned descendant is not stopped: {pid}")
            _pidfd_send_signal(descriptor, signal.SIGKILL)

        while owned and time.monotonic() < deadline:
            for pid, (_start_time, descriptor) in tuple(owned.items()):
                if _pidfd_has_exited(descriptor):
                    os.close(descriptor)
                    owned.pop(pid, None)
                    frozen.discard(pid)
            if owned:
                time.sleep(0.005)
        if owned:
            raise RuntimeError("backtest worker descendants did not stop")
        if _descendant_process_identities(root_pid):
            raise RuntimeError("backtest worker descendants appeared after cleanup")
    except BaseException:
        for _start_time, descriptor in owned.values():
            try:
                _pidfd_send_signal(descriptor, signal.SIGKILL)
            except (OSError, ValueError):
                pass
        while owned:
            for pid, (_start_time, descriptor) in tuple(owned.items()):
                try:
                    exited = _pidfd_has_exited(descriptor)
                except BaseException:
                    # Exit observation is part of ownership proof. Keep the
                    # subreaper and pidfd alive until polling becomes reliable.
                    continue
                if exited:
                    os.close(descriptor)
                    owned.pop(pid, None)
                    frozen.discard(pid)
            if owned:
                time.sleep(0.005)
        raise
    finally:
        for _start_time, descriptor in owned.values():
            try:
                os.close(descriptor)
            except OSError:
                pass


def _put_backtest_process_result(out, result) -> None:
    try:
        _terminate_current_process_descendants()
    except BaseException as cleanup_exc:
        out.put(
            (
                "err",
                cleanup_exc.__class__.__name__,
                str(cleanup_exc),
                traceback.format_exc(),
            )
        )
        return
    out.put(("ok", result))


def _put_backtest_process_error(out, exc: BaseException) -> None:
    original_traceback = traceback.format_exc()
    try:
        _terminate_current_process_descendants()
    except BaseException as cleanup_exc:
        out.put(
            (
                "err",
                cleanup_exc.__class__.__name__,
                f"{exc.__class__.__name__}: {exc}; cleanup failed: {cleanup_exc}",
                traceback.format_exc(),
            )
        )
        return
    out.put(("err", exc.__class__.__name__, str(exc), original_traceback))


def _active_backtest_worker(run_id: str) -> _BacktestWorker | None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        active = _ACTIVE_BACKTEST_WORKERS.get(run_id)
        if active is not None:
            return active
        retained = _RETAINED_BACKTEST_WORKERS.get(run_id, ())
        return retained[0] if retained else None


def _backtest_workers(run_id: str) -> tuple[_BacktestWorker, ...]:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        workers: list[_BacktestWorker] = []
        active = _ACTIVE_BACKTEST_WORKERS.get(run_id)
        if active is not None:
            workers.append(active)
        for retained in _RETAINED_BACKTEST_WORKERS.get(run_id, ()):
            if all(candidate is not retained for candidate in workers):
                workers.append(retained)
        return tuple(workers)


def _backtest_worker_is_starting(run_id: str) -> bool:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        return run_id in _STARTING_BACKTEST_RUNS


def _set_backtest_worker_starting(run_id: str, starting: bool) -> None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if starting:
            _STARTING_BACKTEST_RUNS.add(run_id)
        else:
            _STARTING_BACKTEST_RUNS.discard(run_id)


def _request_backtest_cancel(run_id: str, cancel_requests: set[str]) -> bool:
    """Close worker admission before cancellation starts scanning ownership."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if run_id in _TERMINAL_BACKTEST_RUNS:
            return False
        cancel_requests.add(run_id)
        return True


def _backtest_terminal_outcome(run_id: str) -> str | None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        return _TERMINAL_BACKTEST_OUTCOMES.get(run_id)


def _admit_backtest_worker_start(
    run_id: str, cancel_requests: set[str], worker: _BacktestWorker
) -> bool:
    """Atomically retain the only startup owner unless the run is sealed."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if (
            run_id in cancel_requests
            or run_id in _TERMINAL_BACKTEST_RUNS
            or run_id in _STARTING_BACKTEST_RUNS
            or run_id in _ACTIVE_BACKTEST_WORKERS
            or _RETAINED_BACKTEST_WORKERS.get(run_id)
            or run_id in _RETAINED_BACKTEST_LEASES
        ):
            return False
        _RETAINED_BACKTEST_WORKERS[run_id] = [worker]
        _STARTING_BACKTEST_RUNS.add(run_id)
        return True


def _seal_backtest_terminal_if_quiescent(
    run_id: str,
) -> tuple[tuple[_BacktestWorker, ...], bool]:
    """Atomically snapshot ownership or reserve terminal cancellation."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        workers: list[_BacktestWorker] = []
        active = _ACTIVE_BACKTEST_WORKERS.get(run_id)
        if active is not None:
            workers.append(active)
        for retained in _RETAINED_BACKTEST_WORKERS.get(run_id, ()):
            if all(candidate is not retained for candidate in workers):
                workers.append(retained)
        starting = run_id in _STARTING_BACKTEST_RUNS
        if workers or starting:
            return tuple(workers), False
        existing = _TERMINAL_BACKTEST_OUTCOMES.get(run_id)
        if existing is not None:
            return (), True
        _TERMINAL_BACKTEST_RUNS.add(run_id)
        _TERMINAL_BACKTEST_OUTCOMES[run_id] = "cancelled"
        return (), True


def _seal_backtest_success_if_quiescent(
    run_id: str, cancel_requests: set[str]
) -> tuple[tuple[_BacktestWorker, ...], bool]:
    """Reserve success only when no cancellation or terminal outcome won."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        workers: list[_BacktestWorker] = []
        active = _ACTIVE_BACKTEST_WORKERS.get(run_id)
        if active is not None:
            workers.append(active)
        for retained in _RETAINED_BACKTEST_WORKERS.get(run_id, ()):
            if all(candidate is not retained for candidate in workers):
                workers.append(retained)
        if (
            workers
            or run_id in _STARTING_BACKTEST_RUNS
            or run_id in cancel_requests
            or run_id in _TERMINAL_BACKTEST_RUNS
        ):
            return tuple(workers), False
        _TERMINAL_BACKTEST_RUNS.add(run_id)
        _TERMINAL_BACKTEST_OUTCOMES[run_id] = "success"
        return (), True


def _seal_backtest_failure(
    run_id: str, *, replace_outcome: str | None = None
) -> bool:
    """Reserve terminal failure, optionally replacing this task's reservation."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        existing = _TERMINAL_BACKTEST_OUTCOMES.get(run_id)
        if existing is not None and existing != replace_outcome:
            return False
        _TERMINAL_BACKTEST_RUNS.add(run_id)
        _TERMINAL_BACKTEST_OUTCOMES[run_id] = "failed"
        return True


def _retain_backtest_admission_lease(
    run_id: str, lease: _BacktestLease
) -> bool:
    """Keep a concurrency permit while any worker ownership is unresolved."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        has_ownership = (
            run_id in _ACTIVE_BACKTEST_WORKERS
            or bool(_RETAINED_BACKTEST_WORKERS.get(run_id))
            or run_id in _STARTING_BACKTEST_RUNS
        )
        if not has_ownership:
            return False
        existing = _RETAINED_BACKTEST_LEASES.get(run_id)
        if existing is not None and existing is not lease:
            raise RuntimeError(f"backtest admission lease already retained: {run_id}")
        _RETAINED_BACKTEST_LEASES[run_id] = lease
        return True


def _register_backtest_worker(run_id: str, worker: _BacktestWorker) -> None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        retained = _RETAINED_BACKTEST_WORKERS.get(run_id, [])
        other_retained = [candidate for candidate in retained if candidate is not worker]
        if run_id in _ACTIVE_BACKTEST_WORKERS or other_retained:
            raise RuntimeError(f"backtest worker already registered: {run_id}")
        _RETAINED_BACKTEST_WORKERS.pop(run_id, None)
        _ACTIVE_BACKTEST_WORKERS[run_id] = worker


def _retain_backtest_worker_for_retry(run_id: str, worker: _BacktestWorker) -> None:
    """Retain ownership when post-start cleanup cannot be proven."""

    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if _ACTIVE_BACKTEST_WORKERS.get(run_id) is worker:
            return
        retained = _RETAINED_BACKTEST_WORKERS.setdefault(run_id, [])
        if worker not in retained:
            retained.append(worker)


def _unregister_backtest_worker(run_id: str, worker: _BacktestWorker) -> None:
    retained_lease: _BacktestLease | None = None
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if _ACTIVE_BACKTEST_WORKERS.get(run_id) is worker:
            _ACTIVE_BACKTEST_WORKERS.pop(run_id, None)
        retained = _RETAINED_BACKTEST_WORKERS.get(run_id)
        if retained is not None:
            retained[:] = [candidate for candidate in retained if candidate is not worker]
            if not retained:
                _RETAINED_BACKTEST_WORKERS.pop(run_id, None)
        if (
            run_id not in _ACTIVE_BACKTEST_WORKERS
            and not _RETAINED_BACKTEST_WORKERS.get(run_id)
            and run_id not in _STARTING_BACKTEST_RUNS
        ):
            retained_lease = _RETAINED_BACKTEST_LEASES.pop(run_id, None)
    if retained_lease is not None:
        retained_lease.release()


def _cleanup_event_is_set(worker: _BacktestWorker) -> bool:
    event = worker.cleanup_complete
    is_set = getattr(event, "is_set", None)
    if not callable(is_set):
        return False
    try:
        return bool(is_set())
    except BaseException:
        return False


def _pin_backtest_worker(worker: _BacktestWorker) -> int | None:
    """Open and revalidate a pidfd for the exact live multiprocessing child."""

    pid = worker.owned_pid if worker.owned_pid is not None else worker.process.pid
    if pid is None:
        return None
    expected_start = worker.start_time
    if expected_start is None:
        return None
    before = _proc_identity(pid)
    if before is None or before[0] == "Z":
        return None
    if before[2] != expected_start:
        return None
    descriptor = _pidfd_open(pid)
    try:
        after = _proc_identity(pid)
        if after is None or after[0] == "Z" or after[2] != expected_start:
            os.close(descriptor)
            return None
        worker.owned_pid = pid
        worker.start_time = expected_start
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _backtest_worker_is_alive(worker: _BacktestWorker) -> bool:
    pid = worker.owned_pid
    expected_start = worker.start_time
    if pid is not None and expected_start is not None:
        identity = _proc_identity(pid)
        return (
            identity is not None
            and identity[0] != "Z"
            and identity[2] == expected_start
        )
    return bool(worker.process.is_alive())


def _join_backtest_worker(worker: _BacktestWorker, timeout: float) -> None:
    try:
        worker.process.join(timeout=max(0.0, timeout))
        return
    except (AssertionError, ValueError):
        pid = worker.owned_pid
        if pid is None:
            raise
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited == pid or time.monotonic() >= deadline:
            return
        time.sleep(0.005)


def _terminate_backtest_worker(worker: _BacktestWorker, timeout: float = 3.0) -> bool:
    """Stop the pinned supervisor and require its whole-family cleanup proof."""

    with worker.lock:
        if worker.cleanup_proven:
            _join_backtest_worker(worker, 0)
            return True
        deadline = time.monotonic() + max(0.0, timeout)
        descriptor: int | None = None
        try:
            if _backtest_worker_is_alive(worker):
                descriptor = _pin_backtest_worker(worker)
                if descriptor is None:
                    _join_backtest_worker(worker, 0)
                else:
                    _pidfd_send_signal(descriptor, signal.SIGTERM)

            while _backtest_worker_is_alive(worker) and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                _join_backtest_worker(worker, min(0.05, max(0.0, remaining)))
                if _cleanup_event_is_set(worker) and _backtest_worker_is_alive(worker):
                    if descriptor is None:
                        descriptor = _pin_backtest_worker(worker)
                    if descriptor is not None:
                        _pidfd_send_signal(descriptor, signal.SIGKILL)
                    _join_backtest_worker(
                        worker,
                        max(0.0, deadline - time.monotonic()),
                    )
                    break

            stopped = not _backtest_worker_is_alive(worker)
            if stopped:
                _join_backtest_worker(worker, 0)
            cleanup_proven = stopped and _cleanup_event_is_set(worker)
            if cleanup_proven:
                worker.cleanup_proven = True
            return cleanup_proven
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _cleanup_registered_backtest_worker(
    run_id: str,
    worker: _BacktestWorker,
    timeout: float = 3.0,
) -> bool:
    """Drop registry ownership only after exact-family cleanup is proven."""

    cleanup_proven = _terminate_backtest_worker(worker, timeout=timeout)
    if cleanup_proven:
        _unregister_backtest_worker(run_id, worker)
    return cleanup_proven


def _parse_date_ms(value: str) -> int:
    """Parse ISO date or ms timestamp using the configured default timezone."""
    return int(parse_timestamp_ms(value, 0))


def _market_data_query_for_strategy(
    strategy,
    from_ms: int,
    to_ms: int,
    timeframe: str | None = None,
    symbol: str | None = None,
):
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    return BarQuery(
        instrument=InstrumentKey(
            exchange=strategy.exchange.lower(),
            market=strategy.market_type.lower(),
            symbol=str(symbol or strategy.symbol).upper(),
        ),
        timeframe=parse_timeframe(timeframe or strategy.timeframe),
        start_ms=from_ms,
        end_ms=to_ms,
        gap_policy="allow_with_metadata",
    )


def _confirmed_htf_bars_for_backtest(
    chart_bars,
    *,
    strategy,
    requested_timeframe: str | None,
    mtf_series=None,
    load_bars,
    from_ms: int,
    to_ms: int,
):
    from openpine.runtime.isolated_run import _confirmed_htf_bars_for_timeframe
    from openpine.runtime.mtf import (
        admitted_mtf_requests,
        confirmed_mtf_bars_for_requests,
    )

    requests = admitted_mtf_requests(
        chart_symbol=strategy.symbol,
        htf_timeframe=requested_timeframe,
        mtf_series=mtf_series,
    )
    if requests:
        def load_requested(symbol: str, timeframe: str):
            query = _market_data_query_for_strategy(
                strategy,
                from_ms,
                to_ms,
                timeframe=timeframe,
                symbol=symbol,
            )
            series = load_bars(query)
            return list(getattr(series, "bars", series))

        return confirmed_mtf_bars_for_requests(
            chart_bars=chart_bars,
            chart_symbol=strategy.symbol,
            chart_timeframe=strategy.timeframe,
            requests=requests,
            load_bars=load_requested,
        )

    fetched = None
    if requested_timeframe and str(requested_timeframe) != str(strategy.timeframe):
        query = _market_data_query_for_strategy(strategy, from_ms, to_ms, timeframe=str(requested_timeframe))
        series = load_bars(query)
        fetched = list(getattr(series, "bars", series))
    return _confirmed_htf_bars_for_timeframe(
        chart_bars=chart_bars,
        symbol=str(strategy.symbol).upper(),
        chart_timeframe=str(strategy.timeframe),
        requested_timeframe=requested_timeframe,
        fetched_htf_bars=fetched,
    )


def _estimate_backtest_market_data(
    strategy, from_ms: int, to_ms: int
) -> BacktestEstimateResponse:
    query = _market_data_query_for_strategy(strategy, from_ms, to_ms)
    duration_ms = query.timeframe.duration_ms or 60000
    estimated_bars = max(0, (query.end_ms - query.start_ms) // duration_ms + 1)
    estimated_pages = max(1, (estimated_bars + 999) // 1000)
    return BacktestEstimateResponse(
        strategy_id=strategy.strategy_id,
        symbol=strategy.symbol.upper(),
        timeframe=strategy.timeframe,
        exchange=strategy.exchange.lower(),
        market_type=strategy.market_type.lower(),
        requested_from=from_ms,
        requested_to=to_ms,
        effective_from=query.start_ms,
        effective_to=query.end_ms,
        earliest_available=None,
        adjusted=False,
        estimated_bars=estimated_bars,
        estimated_pages=estimated_pages,
    )


def _backtest_progress_source_label(phase: str, query) -> str:
    if phase.startswith("cache"):
        return "cache"
    return f"{query.instrument.exchange} {query.instrument.market}"


def _progress_ratio(done: float, total: float) -> float:
    if not total or total <= 0:
        return 0.0
    return max(0.0, min(float(done) / float(total), 1.0))


def _backtest_market_data_pct(
    *,
    bars_fetched: int,
    pages_done: int,
    expected_bars: int,
    expected_pages: int,
) -> float:
    """Progress for market-data loading: 20% → 35%, driven by bars/pages."""
    ratio = max(
        _progress_ratio(bars_fetched, expected_bars),
        _progress_ratio(pages_done, expected_pages),
    )
    return 0.20 + 0.15 * ratio


def _backtest_compute_pct(done: int, total: int) -> float:
    """Progress for strategy computation: 35% → 95%."""
    return 0.35 + 0.60 * _progress_ratio(done, total)


def _bar_series_fingerprint(series) -> str:
    digest = hashlib.sha256()
    digest.update(b"openpine.bar_series.v1\0")
    query = series.query
    digest.update(str(query.instrument.exchange).encode())
    digest.update(b"\0")
    digest.update(str(query.instrument.market).encode())
    digest.update(b"\0")
    digest.update(str(query.instrument.symbol).encode())
    digest.update(b"\0")
    digest.update(str(query.timeframe.canonical).encode())
    digest.update(b"\0")
    digest.update(str(query.start_ms).encode())
    digest.update(b"\0")
    digest.update(str(query.end_ms).encode())
    for bar in series.bars:
        digest.update(
            (
                f"{bar.time}|{bar.time_close}|{bar.open:.12g}|{bar.high:.12g}|"
                f"{bar.low:.12g}|{bar.close:.12g}|{bar.volume!r}\n"
            ).encode()
        )
    return digest.hexdigest()


def _admit_loaded_backtest_run(
    state: GatewayState,
    *,
    strategy: object,
    run_id: str,
    artifact: dict[str, Any],
    bars: list[object],
    supplemental_bars: list[dict[str, Any]] | None,
    config: object,
    params: dict[str, Any] | None = None,
) -> dict[str, object]:
    from openpine.admission import DeploymentAdmissionIdentity
    from openpine.run_identity import admit_and_persist_run_identity

    data_dir = getattr(getattr(state, "config", None), "data_dir", None)
    if data_dir is None:
        raise RuntimeError("run identity data directory is unavailable")
    deployment = getattr(state, "admission_identity", None)
    if not isinstance(deployment, DeploymentAdmissionIdentity):
        raise RuntimeError("run admission deployment identity is unavailable")
    admitted_manifest = getattr(state, "admitted_manifest", None)
    if not isinstance(admitted_manifest, dict):
        raise RuntimeError("admitted candidate manifest is unavailable")
    from openpine.runtime.inputs import applied_config_hash, resolve_inputs

    resolved_hash = applied_config_hash(config, resolve_inputs(artifact["python_code"], params))
    object.__setattr__(config, "applied_config_hash", resolved_hash)
    payload = admit_and_persist_run_identity(
        data_dir=data_dir,
        deployment=deployment,
        admitted_manifest=admitted_manifest,
        mode="backtest",
        run_id=run_id,
        artifact=artifact,
        bars=bars,
        supplemental_bars=supplemental_bars,
        exchange=str(getattr(config, "exchange")),
        market=str(getattr(config, "market_type")),
        symbol=str(getattr(config, "symbol")),
        timeframe=str(getattr(config, "timeframe")),
        start_ms=int(getattr(config, "start_time")),
        end_ms=int(getattr(config, "end_time")),
        semantic_profile=str(getattr(config, "semantic_profile")),
        finality_policy="CLOSED_BAR_ONLY",
        warmup_policy="CALC_ONLY",
        score_policy="ALL_BARS",
        required_capabilities=(
            "closed_bar",
            "deterministic_clock",
            "isolated_worker",
            "broker_projection",
            "intent_tape_v2",
        ),
        created_at_utc_ms=int(time.time() * 1000),
        config_hash=resolved_hash,
    )
    return cast(dict[str, object], payload)


def _backtest_process_entry(
    out, adapter, strategy_class, bars, config, params, runtime_data_provider, effective_pre_bars=None
):
    def progress(done: int, total: int) -> None:
        try:
            out.put_nowait(("progress", int(done), int(total)))
        except Exception:
            pass

    try:
        run_kwargs = {
            "params": params,
            "progress_callback": progress,
            "runtime_data_provider": runtime_data_provider,
        }
        if effective_pre_bars is not None:
            run_kwargs["effective_pre_bars"] = effective_pre_bars
        result = adapter.run(
            strategy_class,
            bars,
            config,
            **run_kwargs,
        )
        _put_backtest_process_result(out, result)
    except BaseException as exc:
        _put_backtest_process_error(out, exc)


def _artifact_backtest_process_entry(out, spec: _ArtifactBacktestSpec, bars, config, params):
    """Reconstruct unpicklable runtime objects inside a safe spawned worker."""

    try:
        from openpine.runtime.engine import BacktestEngineAdapter

        source = spec.source
        if not source:
            raise RuntimeError("captured artifact source is missing")
        from openpine.run_identity import execution_data_snapshot_hash

        actual_snapshot_hash = execution_data_snapshot_hash(
            bars=bars,
            supplemental_bars=spec.htf_bars,
            exchange=str(config.exchange),
            market=str(config.market_type),
            symbol=str(config.symbol),
            timeframe=str(config.timeframe),
            start_ms=int(config.start_time),
            end_ms=int(config.end_time),
            finality_policy="CLOSED_BAR_ONLY",
        )
        if actual_snapshot_hash != spec.data_snapshot_hash:
            raise RuntimeError("data snapshot hash mismatch before backtest execution")
        for name, value in (
            ("execution_context", spec.execution_context),
            ("admitted_manifest", spec.admitted_manifest),
            ("instrument_id", spec.execution_context["instrument_id"]),
            ("generated_artifact", spec.generated_artifact),
            ("bar_envelopes", spec.bar_envelopes),
            ("run_hash", spec.run_hash),
            ("protocol_artifact_dir", spec.protocol_artifact_dir),
            ("isolated_protocol", "bulk_backtest"),
        ):
            object.__setattr__(config, name, value)
        htf_bars = spec.htf_bars
        requested = spec.htf_timeframe
        if htf_bars is None and not (
            requested and str(requested) != str(spec.timeframe)
        ):
            from openpine.runtime.isolated_run import _confirmed_htf_bars_from_provider_bars

            htf_bars = _confirmed_htf_bars_from_provider_bars(
                bars,
                symbol=str(spec.symbol),
                timeframe=str(spec.timeframe),
            )
        result = BacktestEngineAdapter().run_isolated(
            source, bars, config, htf_bars=htf_bars, params=params
        )
        _put_backtest_process_result(out, result)
    except BaseException as exc:
        _put_backtest_process_error(out, exc)


def _publish_backtest_startup_identity(sender) -> None:
    if sender is None:
        return
    try:
        pid = os.getpid()
        identity = _proc_identity(pid)
        if identity is None:
            raise RuntimeError("backtest supervisor startup identity is unavailable")
        sender.send((pid, identity[2]))
    finally:
        sender.close()


def _receive_backtest_startup_identity(receiver) -> tuple[int, int] | None:
    if receiver is None:
        return None
    try:
        payload = receiver.recv()
    except EOFError:
        return None
    finally:
        receiver.close()
    if (
        not isinstance(payload, tuple)
        or len(payload) != 2
        or not all(isinstance(value, int) for value in payload)
        or payload[0] <= 0
        or payload[1] < 0
    ):
        raise RuntimeError("invalid backtest supervisor startup identity")
    return payload


def _supervised_backtest_process_entry(
    out,
    process_target,
    process_args: tuple,
    cleanup_complete=None,
    startup_sender=None,
) -> None:
    """Own the subreaper boundary until every callable descendant is gone."""

    if sys.platform != "linux" or not hasattr(os, "fork"):
        _publish_backtest_startup_identity(startup_sender)
        process_target(out, *process_args)
        if cleanup_complete is not None:
            cleanup_complete.set()
        return

    def request_shutdown(_signum, _frame) -> None:
        raise _BacktestSupervisorShutdown()

    signal.signal(signal.SIGTERM, request_shutdown)
    ack_read: int | None = None
    ack_write: int | None = None
    pending_error: tuple[str, str, str, str] | None = None
    shutdown_requested = False
    cleanup_error: BaseException | None = None
    try:
        _publish_backtest_startup_identity(startup_sender)
        if hasattr(os, "setsid"):
            try:
                os.setsid()
            except OSError as exc:
                pending_error = (
                    "err",
                    exc.__class__.__name__,
                    "unable to isolate backtest process group",
                    traceback.format_exc(),
                )
                return
        _enable_child_subreaper()
        ack_read, ack_write = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.close(ack_read)
            exit_code = 0
            try:
                process_target(out, *process_args)
                out.close()
                out.join_thread()
                os.write(ack_write, b"1")
            except BaseException as exc:
                exit_code = 1
                try:
                    out.put(
                        (
                            "err",
                            exc.__class__.__name__,
                            str(exc),
                            traceback.format_exc(),
                        )
                    )
                    out.close()
                    out.join_thread()
                    os.write(ack_write, b"1")
                except BaseException:
                    pass
            finally:
                os.close(ack_write)
            os._exit(exit_code)

        os.close(ack_write)
        ack_write = None
        _, wait_status = os.waitpid(child_pid, 0)
        os.set_blocking(ack_read, False)
        try:
            acknowledged = os.read(ack_read, 1) == b"1"
        except BlockingIOError:
            acknowledged = False
        if not acknowledged:
            exit_code = os.waitstatus_to_exitcode(wait_status)
            pending_error = (
                "err",
                "RuntimeError",
                f"backtest callable exited abruptly with code {exit_code}",
                "",
            )
    except _BacktestSupervisorShutdown:
        shutdown_requested = True
    except BaseException as exc:
        pending_error = (
            "err",
            exc.__class__.__name__,
            str(exc),
            traceback.format_exc(),
        )
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for descriptor in (ack_read, ack_write):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            _terminate_current_process_descendants(timeout=3.0)
            if cleanup_complete is not None:
                cleanup_complete.set()
        except BaseException as exc:
            cleanup_error = exc
        if not shutdown_requested:
            if cleanup_error is not None:
                pending_error = (
                    "err",
                    "RuntimeError",
                    f"backtest supervisor cleanup failed: {cleanup_error}",
                    traceback.format_exc(),
                )
            if pending_error is not None:
                try:
                    out.put(pending_error)
                except BaseException:
                    pass
        try:
            out.close()
            out.join_thread()
        except BaseException:
            pass
    if cleanup_error is not None:
        raise RuntimeError("backtest supervisor cleanup failed") from cleanup_error


def _close_backtest_queue(out) -> None:
    close_error: BaseException | None = None
    try:
        out.close()
    except BaseException as exc:
        close_error = exc
    try:
        out.cancel_join_thread()
    except BaseException as exc:
        if close_error is None:
            close_error = exc
    if close_error is not None:
        raise close_error


def _execute_backtest_process(
    run_id: str,
    cancel_requests: set[str],
    process_target,
    process_args: tuple,
    progress_callback=None,
    context_name: str = "spawn",
):
    ctx = cast(Any, mp.get_context(context_name))
    out = ctx.Queue()
    startup_receiver = None
    startup_sender = None
    startup_channel_created = False
    try:
        cleanup_complete = ctx.Event() if hasattr(ctx, "Event") else None
        if hasattr(ctx, "Pipe"):
            startup_receiver, startup_sender = ctx.Pipe(duplex=False)
            startup_channel_created = True
        proc = ctx.Process(
            target=_supervised_backtest_process_entry,
            args=(
                out,
                process_target,
                process_args,
                cleanup_complete,
                startup_sender,
            ),
        )
        worker = _BacktestWorker(
            process=proc,
            out=out,
            process_group=None,
            start_time=None,
            cleanup_complete=cleanup_complete,
        )
        if not _admit_backtest_worker_start(run_id, cancel_requests, worker):
            raise _BacktestCancelled("Cancelled before compute worker start")
    except BaseException:
        for connection in (startup_receiver, startup_sender):
            if connection is not None:
                connection.close()
        _close_backtest_queue(out)
        raise
    try:
        proc.start()
    except BaseException as start_exc:
        if startup_sender is not None:
            startup_sender.close()
            startup_sender = None
        startup_identity: tuple[int, int] | None = None
        startup_error: BaseException | None = None
        try:
            startup_identity = _receive_backtest_startup_identity(startup_receiver)
        except BaseException as exc:
            startup_error = exc
        finally:
            startup_receiver = None
        pid = getattr(proc, "pid", None)
        if startup_identity is not None:
            startup_pid, startup_start_time = startup_identity
            if pid is not None and pid != startup_pid:
                startup_error = RuntimeError(
                    "backtest supervisor startup PID disagrees with Process.pid"
                )
            worker.owned_pid = startup_pid
            worker.start_time = startup_start_time

        cleanup_ok = False
        start_cleanup_error = startup_error
        if worker.owned_pid is not None:
            try:
                cleanup_ok = _terminate_backtest_worker(worker)
                if not cleanup_ok and start_cleanup_error is None:
                    start_cleanup_error = RuntimeError(
                        "partially started backtest worker did not stop"
                    )
            except BaseException as exc:
                if start_cleanup_error is None:
                    start_cleanup_error = exc
        elif startup_channel_created and startup_error is None:
            # EOF after closing the parent sender proves no live spawned child
            # still owns this per-start capability.
            cleanup_ok = True
        else:
            start_cleanup_error = start_cleanup_error or RuntimeError(
                "partially started backtest worker identity is unavailable"
            )

        queue_cleanup_error: BaseException | None = None
        try:
            _close_backtest_queue(out)
        except BaseException as exc:
            queue_cleanup_error = exc
        finally:
            if cleanup_ok:
                _unregister_backtest_worker(run_id, worker)
            else:
                _retain_backtest_worker_for_retry(run_id, worker)
            _set_backtest_worker_starting(run_id, False)
        if start_cleanup_error is None:
            start_cleanup_error = queue_cleanup_error
        if start_cleanup_error is not None:
            raise RuntimeError(
                f"partially started backtest worker cleanup failed; start failed: {start_exc}"
            ) from start_cleanup_error
        raise
    if startup_sender is not None:
        startup_sender.close()
        startup_sender = None
    startup_setup_exc: BaseException | None = None
    try:
        startup_identity = _receive_backtest_startup_identity(startup_receiver)
    except BaseException as exc:
        startup_identity = None
        startup_setup_exc = exc
    finally:
        startup_receiver = None
    pid = getattr(proc, "pid", None)
    if startup_identity is not None:
        startup_pid, startup_start_time = startup_identity
        if pid is not None and pid != startup_pid:
            startup_setup_exc = RuntimeError(
                "backtest supervisor startup PID disagrees with Process.pid"
            )
        worker.owned_pid = startup_pid
        worker.start_time = startup_start_time
        pid = startup_pid
    elif startup_setup_exc is None:
        startup_setup_exc = RuntimeError(
            "backtest supervisor startup capability closed without exact identity"
        )
    try:
        if startup_setup_exc is not None:
            raise startup_setup_exc
        isolation_deadline = time.monotonic() + _BACKTEST_ISOLATION_TIMEOUT_SECONDS
        verified_process_group: int | None = None
        while pid is not None and proc.is_alive() and time.monotonic() < isolation_deadline:
            identity = _proc_identity(pid)
            if (
                identity is None
                or identity[0] == "Z"
                or worker.start_time is None
                or identity[2] != worker.start_time
            ):
                raise RuntimeError(
                    "backtest supervisor identity drifted after startup capability proof"
                )
            if identity[1] == pid:
                verified_process_group = pid
                break
            time.sleep(0.005)
        worker.process_group = verified_process_group
    except BaseException as setup_exc:
        cleanup_ok = False
        setup_cleanup_exc: BaseException | None = None
        queue_cleanup_exc: BaseException | None = None
        try:
            cleanup_ok = _terminate_backtest_worker(worker)
        except BaseException as exc:
            setup_cleanup_exc = exc
        finally:
            try:
                _close_backtest_queue(out)
            except BaseException as exc:
                queue_cleanup_exc = exc
            finally:
                if cleanup_ok:
                    _unregister_backtest_worker(run_id, worker)
                else:
                    _retain_backtest_worker_for_retry(run_id, worker)
                _set_backtest_worker_starting(run_id, False)
        cleanup_exc = setup_cleanup_exc or queue_cleanup_exc
        if not cleanup_ok or cleanup_exc is not None:
            raise RuntimeError(
                f"backtest worker post-start cleanup was not proven; setup failed: {setup_exc}"
            ) from (cleanup_exc or setup_exc)
        raise
    if pid is not None and proc.is_alive() and verified_process_group is None:
        cleanup_ok = False
        isolation_cleanup_exc: BaseException | None = None
        try:
            cleanup_ok = _terminate_backtest_worker(worker)
        except BaseException as exc:
            isolation_cleanup_exc = exc
        finally:
            try:
                _close_backtest_queue(out)
            except BaseException as exc:
                if isolation_cleanup_exc is None:
                    isolation_cleanup_exc = exc
            finally:
                if cleanup_ok:
                    _unregister_backtest_worker(run_id, worker)
                else:
                    _retain_backtest_worker_for_retry(run_id, worker)
                _set_backtest_worker_starting(run_id, False)
        if not cleanup_ok or isolation_cleanup_exc is not None:
            raise RuntimeError(
                "backtest worker isolation failed and its cleanup was not proven"
            ) from isolation_cleanup_exc
        raise RuntimeError("backtest worker failed to create an isolated process group")
    try:
        _register_backtest_worker(run_id, worker)
    except BaseException as registration_exc:
        cleanup_ok = False
        registration_cleanup_exc: BaseException | None = None
        try:
            cleanup_ok = _terminate_backtest_worker(worker)
        except BaseException as exc:
            registration_cleanup_exc = exc
        finally:
            try:
                _close_backtest_queue(out)
            except BaseException as exc:
                if registration_cleanup_exc is None:
                    registration_cleanup_exc = exc
            finally:
                if cleanup_ok:
                    _unregister_backtest_worker(run_id, worker)
                else:
                    _retain_backtest_worker_for_retry(run_id, worker)
                _set_backtest_worker_starting(run_id, False)
        if not cleanup_ok or registration_cleanup_exc is not None:
            raise RuntimeError(
                f"backtest worker registration cleanup was not proven; registration failed: {registration_exc}"
            ) from (registration_cleanup_exc or registration_exc)
        raise
    _set_backtest_worker_starting(run_id, False)
    final: tuple | None = None
    try:
        while proc.is_alive() or final is None:
            if run_id in cancel_requests or worker.cancel_requested.is_set():
                if not _terminate_backtest_worker(worker):
                    raise RuntimeError("backtest process tree did not stop within the cancellation deadline")
                raise _BacktestCancelled("Cancelled during compute")
            try:
                status, *parts = out.get(timeout=0.1)
            except queue.Empty:
                if not proc.is_alive():
                    if run_id in cancel_requests or worker.cancel_requested.is_set():
                        raise _BacktestCancelled("Cancelled during compute")
                    break
                continue
            if status == "progress":
                if progress_callback is not None:
                    progress_callback(int(parts[0]), int(parts[1]))
                continue
            final = (status, *parts)
            break

        if final is None:
            while True:
                try:
                    status, *parts = out.get_nowait()
                except queue.Empty:
                    break
                if status == "progress":
                    if progress_callback is not None:
                        progress_callback(int(parts[0]), int(parts[1]))
                    continue
                final = (status, *parts)
                break
        if final is None:
            if proc.exitcode == 0:
                raise RuntimeError("backtest worker exited without a result")
            raise RuntimeError(f"backtest worker exited with code {proc.exitcode}")
        status, *parts = final
        if status == "ok":
            return parts[0]
        exc_name, message, tb = parts
        raise RuntimeError(f"{exc_name}: {message}\n{tb}")
    finally:
        final_cleanup_error: BaseException | None = None
        final_cleanup_proven = False
        try:
            final_cleanup_proven = _cleanup_registered_backtest_worker(run_id, worker)
            if not final_cleanup_proven:
                final_cleanup_error = RuntimeError(
                    "backtest process tree did not stop within the cleanup deadline"
                )
        except BaseException as exc:
            final_cleanup_error = exc
        finally:
            try:
                _close_backtest_queue(out)
            except BaseException as exc:
                if final_cleanup_error is None:
                    final_cleanup_error = exc
        if final_cleanup_error is not None:
            raise RuntimeError(
                "backtest process tree did not stop within the cleanup deadline"
            ) from final_cleanup_error


def _execute_backtest_run_in_thread(
    run_id: str,
    cancel_requests: set[str],
    adapter,
    strategy_class,
    bars,
    config,
    params,
    runtime_data_provider,
    progress_callback=None,
    effective_pre_bars=None,
):
    return _execute_backtest_process(
        run_id,
        cancel_requests,
        _backtest_process_entry,
        (
            adapter,
            strategy_class,
            bars,
            config,
            params,
            runtime_data_provider,
            effective_pre_bars,
        ),
        progress_callback,
        "spawn",
    )


def _run_backtest_in_process(
    adapter,
    strategy_class,
    bars,
    config,
    params,
    runtime_data_provider,
    progress_callback=None,
    effective_pre_bars=None,
):
    if not isinstance(adapter, _ArtifactBacktestSpec):
        raise RuntimeError("in-process backtest requires stamped artifact source")
    run_id = getattr(_BACKTEST_THREAD_CONTEXT, "run_id", None)
    cancel_requests = getattr(_BACKTEST_THREAD_CONTEXT, "cancel_requests", None)
    return _execute_backtest_process(
        run_id or f"internal-{time.time_ns()}",
        cancel_requests if cancel_requests is not None else set(),
        _artifact_backtest_process_entry,
        (adapter, bars, config, params),
        progress_callback,
        "spawn",
    )


def _run_owned_backtest(
    run_id: str,
    cancel_requests: set[str],
    adapter,
    strategy_class,
    bars,
    config,
    params,
    runtime_data_provider,
    progress_callback=None,
):
    if not isinstance(adapter, _ArtifactBacktestSpec):
        raise RuntimeError("owned backtest requires stamped artifact source")
    _BACKTEST_THREAD_CONTEXT.run_id = run_id
    _BACKTEST_THREAD_CONTEXT.cancel_requests = cancel_requests
    try:
        return _run_backtest_in_process(
            adapter,
            strategy_class,
            bars,
            config,
            params,
            runtime_data_provider,
            progress_callback,
        )
    finally:
        for name in ("run_id", "cancel_requests"):
            if hasattr(_BACKTEST_THREAD_CONTEXT, name):
                delattr(_BACKTEST_THREAD_CONTEXT, name)


def _ensure_backtest_data_fingerprint_column(state: GatewayState) -> None:
    columns = {
        row[1]
        for row in state.storage.execute("PRAGMA table_info(backtest_runs)").fetchall()
    }
    if "data_fingerprint" in columns:
        return
    state.storage.execute("ALTER TABLE backtest_runs ADD COLUMN data_fingerprint TEXT")
    state.storage.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_runs_data_fingerprint ON backtest_runs(data_fingerprint)"
    )
    state.storage.commit()


def _save_backtest_data_fingerprint(
    state: GatewayState, run_id: str, fingerprint: str
) -> None:
    _ensure_backtest_data_fingerprint_column(state)
    now = int(time.time() * 1000)
    state.storage.execute(
        "UPDATE backtest_runs SET data_fingerprint = ?, updated_at = ? WHERE run_id = ?",
        (fingerprint, now, run_id),
    )
    state.storage.commit()


def _normalize_metrics_payload(metrics: dict | None) -> dict | None:
    if not isinstance(metrics, dict):
        return metrics
    nested_metrics = metrics.get("metrics")
    payload = nested_metrics if isinstance(nested_metrics, dict) else metrics
    normalized = dict(payload)
    if "trades_total" not in normalized and "total_trades" in normalized:
        normalized["trades_total"] = normalized["total_trades"]
    if "total_trades" not in normalized and "trades_total" in normalized:
        normalized["total_trades"] = normalized["trades_total"]
    return normalized


async def _run_backtest_background(
    state: GatewayState,
    strategy_id: str,
    run_id: str,
    from_ms: int,
    to_ms: int,
    params_override: dict | None,
    warmup_bars: int,
    capture_plots: bool,
    initial_capital_override: float | None = None,
    admission_lease: _BacktestLease | None = None,
    semantic_profile: str | None = None,
    htf_timeframe: str | None = None,
    mtf_series: list[dict[str, str]] | None = None,
) -> None:
    """Execute backtest in background, update progress via WebSocket."""
    import asyncio

    terminal_reservation: str | None = None

    async def cancel_if_requested(phase: str) -> bool:
        nonlocal terminal_reservation
        if run_id not in state.backtest_cancel_requests:
            return False
        if _backtest_terminal_outcome(run_id) is not None:
            return True
        workers, sealed = _seal_backtest_terminal_if_quiescent(run_id)
        if not sealed:
            if _backtest_terminal_outcome(run_id) is not None:
                return True
            raise RuntimeError(
                f"Backtest cancellation reached {phase} with {len(workers)} owned workers"
            )
        terminal_reservation = "cancelled"
        state.backtest_store.mark_cancelled(run_id, f"Cancelled during {phase}")
        state.backtest_cancel_requests.discard(run_id)
        ws_manager.update_progress(
            run_id, "backtest", "cancelled", 0.0, f"Cancelled during {phase}"
        )
        await ws_manager.broadcast_progress(run_id)
        return True

    try:
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.0, "Loading strategy..."
        )

        registry = state.strategy_registry
        try:
            strategy = registry.get_strategy(strategy_id)
        except KeyError:
            message = "Strategy not found"
            state.backtest_store.mark_failed(run_id, message)
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.0, message
            )
            await ws_manager.broadcast_progress(run_id)
            return

        # Load artifact
        if await cancel_if_requested("strategy load"):
            return
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.1, "Loading artifact..."
        )
        await ws_manager.broadcast_progress(run_id)

        from openpine.runtime.engine import BacktestArtifactError
        from openpine.runtime.isolated_run import IsolatedRunError
        from openpine.run_identity import verified_generated_source

        try:
            artifact = state.artifact_store.get_artifact(
                strategy.artifact_id, strategy.pine_id
            )
            generated_source = verified_generated_source(artifact)
        except (
            AdmitError,
            BacktestArtifactError,
            FileNotFoundError,
            IsolatedRunError,
            ValueError,
        ) as exc:
            ws_manager.update_progress(run_id, "backtest", "failed", 0.1, str(exc))
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, str(exc))
            return

        # Load bars
        if await cancel_if_requested("artifact load"):
            return
        query = _market_data_query_for_strategy(strategy, from_ms, to_ms)
        estimate = _estimate_backtest_market_data(strategy, from_ms, to_ms)
        ws_manager.update_progress(
            run_id,
            "backtest",
            "running",
            0.2,
            f"Preparing market data: 0/{estimate.estimated_bars:,} bars",
            detail={
                "phase": "market_data",
                "bars_processed": 0,
                "total_bars": estimate.estimated_bars,
                "pages_processed": 0,
                "total_pages": estimate.estimated_pages,
                "requested_from": from_ms,
                "effective_from": estimate.effective_from,
                "earliest_available": getattr(estimate, "earliest_available", None),
                "adjusted": getattr(estimate, "adjusted", False),
            },
        )
        await ws_manager.broadcast_progress(run_id)

        def bar_load_progress(
            bars_fetched: int,
            pages: int,
            total_bars: int | None = None,
            total_pages: int | None = None,
            earliest_open_ms: int | None = None,
            phase: str = "fetch",
        ) -> None:
            expected_bars = total_bars or estimate.estimated_bars
            expected_pages = total_pages or estimate.estimated_pages
            pct = _backtest_market_data_pct(
                bars_fetched=bars_fetched,
                pages_done=pages,
                expected_bars=expected_bars,
                expected_pages=expected_pages,
            )
            source = _backtest_progress_source_label(phase, query)
            ws_manager.update_progress(
                run_id,
                "backtest",
                "running",
                pct,
                f"Loading bars from {source}: {bars_fetched:,}/{expected_bars:,} bars "
                f"({pages}/{expected_pages} pages)",
                detail={
                    "phase": phase,
                    "bars_processed": bars_fetched,
                    "total_bars": expected_bars,
                    "pages_processed": pages,
                    "total_pages": expected_pages,
                    "requested_from": from_ms,
                    "effective_from": estimate.effective_from,
                    "earliest_available": earliest_open_ms,
                    "adjusted": estimate.adjusted,
                },
            )

        try:
            loop = asyncio.get_event_loop()
            series = await loop.run_in_executor(
                None,
                lambda: state.orchestrator.load_bars(
                    query, progress_callback=bar_load_progress
                ),
            )
            bars = list(series.bars)
        except Exception as exc:
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.2, f"Data load failed: {exc}"
            )
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, f"Data load failed: {exc}")
            return

        if not bars:
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.2, "No bars found"
            )
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, "No bars found in range")
            return

        if await cancel_if_requested("market data load"):
            return

        data_fingerprint = _bar_series_fingerprint(series)
        try:
            _save_backtest_data_fingerprint(state, run_id, data_fingerprint)
        except Exception as exc:
            log.warning(
                "backtest_data_fingerprint_save_failed", run_id=run_id, error=str(exc)
            )

        total_bars = len(bars)
        ws_manager.update_progress(
            run_id,
            "backtest",
            "running",
            0.35,
            f"Running backtest on {total_bars:,} bars...",
            detail={
                "bars_processed": 0,
                "total_bars": total_bars,
                "phase": "compute",
                "data_fingerprint": data_fingerprint,
            },
        )
        await ws_manager.broadcast_progress(run_id)

        # Build config — read declaration args from artifact
        if await cancel_if_requested("backtest setup"):
            return
        # Read strategy declaration args (calc_on_order_fills, commission, etc.)
        from openpine.runtime.declaration_args import artifact_strategy_declaration_args
        from openpine.runtime.engine import BacktestRunConfig

        decl_args = artifact_strategy_declaration_args(artifact)

        params = {}
        if params_override is not None:
            params = params_override
        elif strategy.params_json:
            import json

            params = json.loads(strategy.params_json)

        # Map commission_type aliases
        commission_type = {
            "cash_per_order": "fixed_per_order",
            "cash_per_contract": "fixed_per_contract",
        }.get(
            str(decl_args.get("commission_type", "none")),
            decl_args.get("commission_type", "none"),
        )

        config = BacktestRunConfig(
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            start_time=from_ms,
            end_time=to_ms,
            exchange=strategy.exchange,
            market_type=strategy.market_type,
            initial_capital=(
                initial_capital_override
                if initial_capital_override is not None
                else decl_args.get("initial_capital", 10000.0)
            ),
            default_qty_type=decl_args.get("default_qty_type", "fixed"),
            default_qty_value=decl_args.get("default_qty_value", 1.0),
            commission_type=commission_type or "none",
            commission_value=decl_args.get("commission_value", 0.0),
            slippage=decl_args.get("slippage", 0.0),
            slippage_type=decl_args.get("slippage_type", "tick"),
            exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
            pyramiding=decl_args.get("pyramiding", 0),
            margin_long=decl_args.get("margin_long", 100.0),
            margin_short=decl_args.get("margin_short", 100.0),
            process_orders_on_close=bool(
                decl_args.get("process_orders_on_close", False)
            ),
            calc_on_order_fills=bool(decl_args.get("calc_on_order_fills", False)),
            calc_on_every_tick=bool(decl_args.get("calc_on_every_tick", False)),
            use_bar_magnifier=bool(decl_args.get("use_bar_magnifier", False)),
            qty_step=default_qty_step(
                strategy.exchange, strategy.market_type, strategy.symbol
            ),
            qty_rounding_mode=default_qty_rounding_mode(
                strategy.exchange, strategy.market_type, strategy.symbol
            ),
            mintick=default_price_tick(
                strategy.exchange, strategy.market_type, strategy.symbol
            ) or 0.01,
            export_resume_state=False,
            content_hash_enabled=True,
            collect_events=True,
            collect_order_lifecycle=True,
            capture_plots=capture_plots,
            semantic_profile=admit_strategy_semantic_profile(
                strategy,
                source="backtest",
                requested_profile=semantic_profile,
            ).value,
        )

        # Reconstruct unpicklable runtime objects inside a spawned worker.  This avoids
        # forking the multithreaded API process while preserving process-tree cancellation.
        state_config = getattr(state, "config", None)
        configured_cache_root = getattr(state_config, "data_cache_root", None)
        configured_data_dir = getattr(state_config, "data_dir", None) or Path(".")
        cache_dir = (
            configured_cache_root or (configured_data_dir / "cache")
        ) / "marketdata"
        stamped_htf = _confirmed_htf_bars_for_backtest(
            bars,
            strategy=strategy,
            requested_timeframe=htf_timeframe,
            mtf_series=mtf_series,
            load_bars=state.orchestrator.load_bars,
            from_ms=from_ms,
            to_ms=to_ms,
        )
        run_identity = _admit_loaded_backtest_run(
            state,
            strategy=strategy,
            run_id=run_id,
            artifact=artifact,
            bars=bars,
            supplemental_bars=stamped_htf,
            config=config,
            params=params,
        )
        canonical_bars = getattr(series, "canonical_bars", None)
        if not isinstance(canonical_bars, (list, tuple)) or len(canonical_bars) != len(
            bars
        ):
            raise RuntimeError("canonical marketdata bar envelopes are required")
        deployment = getattr(state, "admission_identity", None)
        admitted_manifest = getattr(state, "admitted_manifest", None)
        from openpine.admission import DeploymentAdmissionIdentity

        if not isinstance(deployment, DeploymentAdmissionIdentity) or not isinstance(
            admitted_manifest, dict
        ):
            raise RuntimeError("deployment admission identity is required")
        generated_artifact = artifact.get("generated_artifact")
        if not isinstance(generated_artifact, dict):
            raise RuntimeError("generated artifact envelope is required")
        from openpine.run_identity import execution_context_from_admission

        first_bar = canonical_bars[0]
        execution_context = execution_context_from_admission(
            deployment,
            admitted_manifest,
            run_id=run_id,
            strategy_id=strategy.strategy_id,
            artifact=artifact,
            data_snapshot_hash=str(run_identity["data_snapshot_hash"]),
            series_id=str(first_bar["series_id"]),
            instrument_id=str(first_bar["instrument_id"]),
            exchange=config.exchange,
            market=config.market_type,
            symbol=config.symbol,
            timeframe=config.timeframe,
            semantic_profile=config.semantic_profile,
            created_at_utc_ms=int(time.time() * 1000),
        )
        artifact_spec = _ArtifactBacktestSpec(
            pine_id=strategy.pine_id,
            artifact_id=strategy.artifact_id,
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            cache_dir=str(cache_dir),
            exchange=config.exchange,
            market=config.market_type,
            prefetch_end_ms=to_ms,
            source=generated_source,
            data_snapshot_hash=str(run_identity["data_snapshot_hash"]),
            execution_context=execution_context,
            admitted_manifest=admitted_manifest,
            generated_artifact=generated_artifact,
            run_hash=str(run_identity["content_hash"]),
            bar_envelopes=[dict(item) for item in canonical_bars],
            protocol_artifact_dir=str(
                Path(configured_data_dir) / "protocol" / run_id
            ),
            htf_bars=stamped_htf,
            htf_timeframe=htf_timeframe,
        )

        def progress_callback(done: int, total: int) -> None:
            pct = _backtest_compute_pct(done, total)
            ws_manager.update_progress(
                run_id,
                "backtest",
                "running",
                pct,
                f"Bars: {done}/{total}",
                detail={
                    "bars_processed": done,
                    "total_bars": total,
                    "phase": "compute",
                },
            )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run_owned_backtest(
                run_id,
                state.backtest_cancel_requests,
                artifact_spec,
                None,
                bars,
                config,
                params,
                None,
                progress_callback,
            ),
        )

        if await cancel_if_requested("compute"):
            return

        workers, success_sealed = _seal_backtest_success_if_quiescent(
            run_id, state.backtest_cancel_requests
        )
        if not success_sealed:
            if _backtest_terminal_outcome(run_id) is not None:
                return
            if await cancel_if_requested("compute"):
                return
            raise RuntimeError(
                f"Backtest success reached terminal publication with {len(workers)} owned workers"
            )
        terminal_reservation = "success"

        # Save results
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.97, "Saving results..."
        )
        await ws_manager.broadcast_progress(run_id)

        state.backtest_store.save_result(
            run_id=run_id,
            result=result.raw_result,
            trades=getattr(result.raw_result, "trades", []) or [],
            equity_curve=getattr(result.raw_result, "equity_curve", None),
            plots=getattr(result.raw_result, "plots", None) if capture_plots else None,
        )

        ws_manager.update_progress(
            run_id,
            "backtest",
            "completed",
            1.0,
            f"Done. {result.bars_processed} bars processed.",
            detail={
                "bars_processed": result.bars_processed,
                "total_bars": result.bars_processed,
                "phase": "completed",
                "data_fingerprint": data_fingerprint,
            },
        )
        await ws_manager.broadcast_progress(run_id)
        log.info("backtest_completed", run_id=run_id, bars=result.bars_processed)
        state.backtest_cancel_requests.discard(run_id)

    except _BacktestCancelled as exc:
        message = str(exc) or "Cancelled during compute"
        if _backtest_terminal_outcome(run_id) is not None:
            return
        workers, sealed = _seal_backtest_terminal_if_quiescent(run_id)
        if not sealed:
            if _backtest_terminal_outcome(run_id) is not None:
                return
            raise RuntimeError(
                f"Backtest cancellation completed with {len(workers)} owned workers"
            ) from exc
        terminal_reservation = "cancelled"
        state.backtest_store.mark_cancelled(run_id, message)
        ws_manager.update_progress(run_id, "backtest", "cancelled", 0.0, message)
        await ws_manager.broadcast_progress(run_id)
        state.backtest_cancel_requests.discard(run_id)
    except Exception as exc:
        existing_outcome = _backtest_terminal_outcome(run_id)
        if existing_outcome is not None and terminal_reservation is None:
            log.warning(
                "backtest_terminal_outcome_already_chosen",
                run_id=run_id,
                outcome=existing_outcome,
                ignored_error=str(exc),
            )
            return
        if not _seal_backtest_failure(
            run_id, replace_outcome=terminal_reservation
        ):
            log.warning(
                "backtest_failure_reservation_conflict",
                run_id=run_id,
                outcome=_backtest_terminal_outcome(run_id),
                ignored_error=str(exc),
            )
            return
        terminal_reservation = "failed"
        log.error("backtest_failed", run_id=run_id, error=str(exc))
        ws_manager.update_progress(run_id, "backtest", "failed", 0.0, str(exc))
        await ws_manager.broadcast_progress(run_id)
        try:
            state.backtest_store.mark_failed(run_id, str(exc))
        except Exception:
            pass
        state.backtest_cancel_requests.discard(run_id)
    finally:
        if admission_lease is not None:
            if _retain_backtest_admission_lease(run_id, admission_lease):
                admission_lease = None
            if admission_lease is not None:
                admission_lease.release()


@router.post("/run", response_model=BacktestRunResponse)
async def run_backtest(
    body: BacktestRunRequest,
    background_tasks: BackgroundTasks,
    state: GatewayState = Depends(get_state),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ] = None,
) -> BacktestRunResponse:
    """Start a backtest run (async, tracks progress via WebSocket)."""
    require_http_admit(state, "backtest")
    from_ms = _parse_date_ms(body.from_time)
    to_ms = _parse_date_ms(body.to_time)
    if from_ms >= to_ms:
        raise HTTPException(400, "from_time must be before to_time")

    registry = state.strategy_registry
    try:
        strategy = registry.get_strategy(body.strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {body.strategy_id}")

    if getattr(strategy, "archived", False):
        raise HTTPException(400, "Archived strategy is not available for backtests")

    if not strategy.pine_id or not strategy.artifact_id:
        raise HTTPException(
            400, "Strategy has no pine_id or artifact_id. Compile first."
        )

    try:
        admitted_profile = admit_strategy_semantic_profile(
            strategy,
            source="backtest",
            requested_profile=getattr(body, "semantic_profile", None),
        )
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc

    estimate = _estimate_backtest_market_data(strategy, from_ms, to_ms)
    if estimate.effective_from >= estimate.effective_to:
        raise HTTPException(400, "No listed market data found in selected range")

    idempotency_claimed = False
    idempotency_request_hash: str | None = None
    idempotency_claim_token: str | None = None
    if idempotency_key is not None:
        idempotency_request_hash = _backtest_request_hash(body)
        idempotency_claim = _claim_backtest_idempotency(
            state, idempotency_key, idempotency_request_hash
        )
        if idempotency_claim.result_id is not None:
            existing_run = state.backtest_store.get_run(idempotency_claim.result_id)
            if existing_run is None:
                raise HTTPException(409, "The idempotent backtest result is no longer available")
            return BacktestRunResponse(
                run_id=existing_run.run_id,
                strategy_id=existing_run.strategy_id,
                status=existing_run.status,
                started_at=existing_run.started_at,
            )
        idempotency_claimed = True
        idempotency_claim_token = idempotency_claim.claim_token

    from openpine.storage.backtest_dto import BacktestRunRequest as BTRequest

    admission_lease = _backtest_admission_limiter(state).try_acquire()
    if admission_lease is None:
        if idempotency_claimed:
            assert idempotency_key is not None
            assert idempotency_request_hash is not None
            assert idempotency_claim_token is not None
            _release_backtest_idempotency(
                state,
                idempotency_key,
                idempotency_request_hash,
                idempotency_claim_token,
            )
        raise HTTPException(
            429,
            "Backtest capacity is saturated; retry after an active run finishes",
            headers={"Retry-After": "5"},
        )
    try:
        run_id = state.backtest_store.create_run(
            BTRequest(
                strategy_id=body.strategy_id,
                pine_id=strategy.pine_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                exchange=strategy.exchange,
                market_type=strategy.market_type,
                symbol=strategy.symbol,
                price_type="trade",
                timeframe=strategy.timeframe,
                from_time=estimate.effective_from,
                to_time=estimate.effective_to,
                warmup_bars=body.warmup_bars,
            )
        )
    except BaseException as exc:
        admission_lease.release()
        if idempotency_claimed:
            assert idempotency_key is not None
            assert idempotency_request_hash is not None
            assert idempotency_claim_token is not None
            _release_backtest_idempotency(
                state,
                idempotency_key,
                idempotency_request_hash,
                idempotency_claim_token,
            )
        if isinstance(exc, Exception):
            log.error(
                "backtest_storage_unavailable",
                operation="create_run",
                error_type=exc.__class__.__name__,
            )
            raise HTTPException(503, "Backtest storage is unavailable") from exc
        raise

    persist_gateway_job(
        state,
        job_id=str(run_id),
        kind="backtest",
        actor="gateway",
        idempotency_key=idempotency_key,
        input_artifact_refs=[str(strategy.artifact_id)],
        semantic_profile=admitted_profile.value,
    )

    if idempotency_claimed:
        assert idempotency_key is not None
        assert idempotency_request_hash is not None
        assert idempotency_claim_token is not None
        try:
            _complete_backtest_idempotency(
                state,
                idempotency_key,
                idempotency_request_hash,
                idempotency_claim_token,
                run_id,
            )
        except BaseException:
            admission_lease.release()
            try:
                state.backtest_store.mark_failed(
                    run_id, "Failed to persist idempotency mapping"
                )
            except Exception as cleanup_exc:
                log.error(
                    "backtest_idempotency_cleanup_failed",
                    operation="mark_failed",
                    error_type=cleanup_exc.__class__.__name__,
                )
            try:
                _release_backtest_idempotency(
                    state,
                    idempotency_key,
                    idempotency_request_hash,
                    idempotency_claim_token,
                )
            except HTTPException as cleanup_exc:
                log.error(
                    "backtest_idempotency_cleanup_failed",
                    operation="release_claim",
                    status_code=cleanup_exc.status_code,
                )
            raise

    queued_message = "Backtest queued"
    if estimate.adjusted:
        queued_message = (
            f"Backtest queued. Range adjusted to listed data: "
            f"{estimate.estimated_bars:,} bars ({estimate.estimated_pages} pages)."
        )
    try:
        ws_manager.update_progress(
            run_id,
            "backtest",
            "queued",
            0.0,
            queued_message,
            detail=estimate.model_dump(),
        )
        background_tasks.add_task(
            _run_backtest_background,
            state,
            body.strategy_id,
            run_id,
            estimate.effective_from,
            estimate.effective_to,
            body.params_override,
            body.warmup_bars,
            body.capture_plots,
            body.initial_capital,
            admission_lease,
            semantic_profile=admitted_profile.value,
            htf_timeframe=getattr(body, "htf_timeframe", None),
            mtf_series=[
                item.model_dump()
                for item in (getattr(body, "mtf_series", None) or [])
            ],
        )
    except BaseException as exc:
        admission_lease.release()
        try:
            state.backtest_store.mark_failed(run_id, "Failed to schedule backtest")
        except Exception as cleanup_exc:
            log.error(
                "backtest_schedule_cleanup_failed",
                error_type=cleanup_exc.__class__.__name__,
            )
        if isinstance(exc, Exception):
            raise HTTPException(503, "Unable to schedule backtest") from exc
        raise

    log.info("backtest_started", run_id=run_id, strategy_id=body.strategy_id)
    return BacktestRunResponse(
        run_id=run_id,
        strategy_id=body.strategy_id,
        status="queued",
        started_at=int(time.time() * 1000),
    )


@router.get("/estimate", response_model=BacktestEstimateResponse)
async def estimate_backtest(
    strategy_id: str,
    from_time: str,
    to_time: str,
    state: GatewayState = Depends(get_state),
) -> BacktestEstimateResponse:
    """Estimate effective market data range, bars, and provider fetch pages."""
    from_ms = _parse_date_ms(from_time)
    to_ms = _parse_date_ms(to_time)
    if from_ms >= to_ms:
        raise HTTPException(400, "from_time must be before to_time")
    try:
        strategy = state.strategy_registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    if getattr(strategy, "archived", False):
        raise HTTPException(400, "Archived strategy is not available for backtests")
    return _estimate_backtest_market_data(strategy, from_ms, to_ms)


@router.get("/runs", response_model=list[BacktestRunDetail])
async def list_runs(
    strategy_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    state: GatewayState = Depends(get_state),
) -> list[BacktestRunDetail]:
    """List backtest runs."""
    store = state.backtest_store
    registry = state.strategy_registry
    if strategy_id:
        runs = store.list_runs(strategy_id, limit=limit)
    else:
        runs = store.list_all_runs(limit=limit)

    # Compute version per strategy: count of prior runs + 1
    version_counter: dict[str, int] = {}
    run_versions: dict[str, int] = {}
    for r in sorted(runs, key=lambda x: x.started_at or 0):
        version_counter[r.strategy_id] = version_counter.get(r.strategy_id, 0) + 1
        run_versions[r.run_id] = version_counter[r.strategy_id]

    result = []
    for r in runs:
        # Look up strategy name
        strategy_name = None
        try:
            strat = registry.get_strategy(r.strategy_id)
            strategy_name = strat.name
        except (KeyError, Exception):
            pass
        metrics = None
        try:
            metrics = _normalize_metrics_payload(store.get_metrics(r.run_id))
        except Exception:
            pass

        result.append(
            BacktestRunDetail(
                run_id=r.run_id,
                strategy_id=r.strategy_id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                symbol=r.symbol,
                timeframe=r.timeframe,
                from_time=r.from_time,
                to_time=r.to_time,
                bars_processed=getattr(r, "bars_processed", None),
                metrics=metrics,
                strategy_name=strategy_name,
                version=run_versions.get(r.run_id),
            )
        )
    return result


@router.get("/runs/{run_id}", response_model=BacktestRunDetail)
async def get_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> BacktestRunDetail:
    """Get details of a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    metrics = None
    try:
        metrics = _normalize_metrics_payload(state.backtest_store.get_metrics(run_id))
    except Exception:
        pass

    strategy_name = None
    try:
        strat = state.strategy_registry.get_strategy(run.strategy_id)
        strategy_name = strat.name
    except (KeyError, Exception):
        pass

    # Compute version: count of runs for this strategy up to and including this one
    all_runs = state.backtest_store.list_runs(run.strategy_id, limit=1000)
    version = 1
    for i, r in enumerate(sorted(all_runs, key=lambda x: x.started_at or 0), 1):
        if r.run_id == run_id:
            version = i
            break

    return BacktestRunDetail(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        symbol=run.symbol,
        timeframe=run.timeframe,
        from_time=run.from_time,
        to_time=run.to_time,
        metrics=metrics,
        strategy_name=strategy_name,
        version=version,
    )


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> None:
    """Delete a backtest run and all associated data."""
    deleted = state.backtest_store.delete_run(run_id)
    if not deleted:
        raise HTTPException(404, f"Run not found: {run_id}")


@router.post("/runs/{run_id}/action")
async def run_action(
    run_id: str,
    action: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Cancel a run and wait for any owned compute process tree to stop."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    if action != "cancel":
        raise HTTPException(400, f"Unsupported backtest action: {action}")
    import asyncio

    if not _request_backtest_cancel(run_id, state.backtest_cancel_requests):
        return {
            "run_id": run_id,
            "action": action,
            "status": run.status,
            "accepted": False,
        }

    async def cleanup_all_owned_workers() -> tuple[bool, bool]:
        deadline = time.monotonic() + 10.0
        found = False
        while True:
            workers, sealed = _seal_backtest_terminal_if_quiescent(run_id)
            if sealed:
                return True, found
            if not workers:
                if time.monotonic() >= deadline:
                    return False, found
                await asyncio.sleep(0.01)
                continue
            found = True
            round_proven = True
            for owned_worker in workers:
                owned_worker.cancel_requested.set()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    round_proven = False
                    continue
                stopped = await asyncio.to_thread(
                    _cleanup_registered_backtest_worker,
                    run_id,
                    owned_worker,
                    min(3.0, remaining),
                )
                if not stopped:
                    round_proven = False
            if not round_proven:
                return False, found

    if run.status not in {"queued", "running"}:
        stopped, _found = await cleanup_all_owned_workers()
        if not stopped:
            raise HTTPException(503, "Backtest process trees did not stop")
        state.backtest_cancel_requests.discard(run_id)
        return {
            "run_id": run_id,
            "action": action,
            "status": run.status,
            "accepted": False,
        }
    stopped, found = await cleanup_all_owned_workers()
    if not stopped:
        raise HTTPException(503, "Backtest process trees did not stop")
    if found:
        message = "Cancelled during compute"
        state.backtest_store.mark_cancelled(run_id, message)
        state.backtest_cancel_requests.discard(run_id)
        ws_manager.update_progress(
            run_id, "backtest", "cancelled", 0.0, message
        )
        await ws_manager.broadcast_progress(run_id)
        return {
            "run_id": run_id,
            "action": action,
            "status": "cancelled",
            "accepted": True,
        }

    ws_manager.update_progress(
        run_id, "backtest", "cancelling", 0.0, "Cancel requested"
    )
    await ws_manager.broadcast_progress(run_id)
    return {
        "run_id": run_id,
        "action": action,
        "status": "cancelling",
        "accepted": False,
    }


@router.get("/runs/{run_id}/trades", response_model=list[BacktestTradeResponse])
async def get_run_trades(
    run_id: str,
    state: GatewayState = Depends(get_state),
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BacktestTradeResponse]:
    """Get one bounded trade-log page for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    trades = state.backtest_store.list_trades(run_id, limit=limit, offset=offset)
    return [
        BacktestTradeResponse(
            trade_id=t.trade_id,
            run_id=run_id,
            entry_id=getattr(t, "entry_id", None),
            exit_id=getattr(t, "exit_id", None),
            entry_time=t.entry_time,
            exit_time=t.exit_time,
            direction=t.direction,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            stop_price=getattr(t, "stop_price", None),
            take_profit_price=getattr(t, "take_profit_price", None),
            qty=t.qty,
            net_profit=t.net_pnl,
            gross_profit=getattr(t, "gross_pnl", None),
            bars_held=getattr(t, "bars_held", None),
            exit_reason=getattr(t, "exit_reason", None),
        )
        for t in trades
    ]


@router.get("/progress/{run_id}", response_model=BacktestProgress | None)
async def get_progress(run_id: str) -> BacktestProgress | None:
    """Get current progress of a backtest run."""
    p = ws_manager.get_progress(run_id)
    if p is None:
        return None

    detail = p.get("detail") or {}
    if (
        isinstance(detail, dict)
        and "bars_processed" in detail
        and "total_bars" in detail
    ):
        return BacktestProgress(
            run_id=p["operation_id"],
            status=p["status"],
            bars_processed=int(detail.get("bars_processed") or 0),
            total_bars=int(detail.get("total_bars") or 0),
            pct=p["pct"],
            message=p.get("message", ""),
        )

    # Parse bars info from messages like "Bars: 5000/100000"
    import re

    message = p.get("message", "")
    bars_processed = 0
    total_bars = 0
    m = re.search(r"Bars:\s*([\d,]+)\s*/\s*([\d,]+)", message)
    if m:
        bars_processed = int(m.group(1).replace(",", ""))
        total_bars = int(m.group(2).replace(",", ""))

    return BacktestProgress(
        run_id=p["operation_id"],
        status=p["status"],
        bars_processed=bars_processed,
        total_bars=total_bars,
        pct=p["pct"],
        message=message,
    )


@router.get("/progress/{run_id}/detail")
async def get_progress_detail(run_id: str) -> dict[str, object] | None:
    """Get detailed progress including error message."""
    progress = ws_manager.get_progress(run_id)
    return dict(progress) if isinstance(progress, dict) else None


# ── Backtest output routes ────────────────────────────────────────────────────


def _read_parquet_as_csv(path: str) -> str:
    """Read a parquet file and return as CSV string."""
    import pandas as pd

    df = pd.read_parquet(path)
    return str(df.to_csv(index=False))


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _backtest_artifact_root(state: GatewayState) -> Path | None:
    data_dir = getattr(state.backtest_store, "_data_dir", None)
    if data_dir is None:
        return None
    return Path(data_dir).expanduser().resolve()


def _safe_backtest_artifact_path(state: GatewayState, raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser()
    root = _backtest_artifact_root(state)
    if root is None:
        return path
    resolved = path.resolve(strict=False)
    if not _path_is_under(resolved, root):
        log.warning(
            "unsafe_backtest_artifact_path",
            path=str(path),
            allowed_root=str(root),
        )
        return None
    return path


def _export_artifact_summary(state: GatewayState, artifact) -> dict[str, object]:
    summary: dict[str, object] = {"type": artifact.artifact_type}
    path = _safe_backtest_artifact_path(state, str(artifact.path))
    if path is not None:
        summary["filename"] = path.name
    return summary


def _get_artifact_path(
    state: GatewayState, run_id: str, artifact_type: str
) -> str | None:
    """Find artifact path by type for a run."""
    artifacts = state.backtest_store.list_artifacts(run_id)
    for a in artifacts:
        if a.artifact_type == artifact_type:
            path = _safe_backtest_artifact_path(state, str(a.path))
            return str(path) if path is not None else None
    return None


@router.get("/runs/{run_id}/equity")
async def get_run_equity(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get equity curve data for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "equity_curve")
    if path is None:
        raise HTTPException(404, "Equity curve not available for this run")

    try:
        csv_data = _read_parquet_as_csv(path)
        return {"run_id": run_id, "format": "csv", "data": csv_data}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read equity curve: {exc}")


@router.get("/runs/{run_id}/plots")
async def get_run_plots(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get plot outputs data for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "plot_outputs")
    if path is None:
        raise HTTPException(404, "Plot outputs not available for this run")

    try:
        csv_data = _read_parquet_as_csv(path)
        return {"run_id": run_id, "format": "csv", "data": csv_data}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read plot outputs: {exc}")


@router.get("/runs/{run_id}/bar-outputs")
async def get_run_bar_outputs(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get bar-level outputs for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "bar_outputs")
    if path is None:
        raise HTTPException(404, "Bar outputs not available for this run")

    try:
        csv_data = _read_parquet_as_csv(path)
        return {"run_id": run_id, "format": "csv", "data": csv_data}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read bar outputs: {exc}")


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get the markdown report for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "report_md")
    if path is None:
        raise HTTPException(404, "Report not available for this run")

    from pathlib import Path

    try:
        content = Path(path).read_text(encoding="utf-8")
        return {"run_id": run_id, "format": "markdown", "data": content}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read report: {exc}")


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Export all backtest artifacts as a summary (equity + trades + metrics + report)."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    result: dict[str, object] = {"run_id": run_id, "strategy_id": run.strategy_id}

    # Metrics
    metrics = state.backtest_store.get_metrics(run_id)
    if metrics:
        result["metrics"] = metrics

    # Trades
    trades = state.backtest_store.list_trades(run_id)
    result["trades"] = [
        {
            "trade_id": t.trade_id,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_price": getattr(t, "stop_price", None),
            "take_profit_price": getattr(t, "take_profit_price", None),
            "qty": t.qty,
            "net_profit": t.net_pnl,
        }
        for t in trades
    ]

    # Artifacts list
    artifacts = state.backtest_store.list_artifacts(run_id)
    result["artifacts"] = [_export_artifact_summary(state, a) for a in artifacts]

    return result
