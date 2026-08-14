"""Backtest routes — run, progress, results."""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing as mp
import os
import queue
import signal
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
from openpine.exchange_metadata import (
    default_price_tick,
    default_qty_rounding_mode,
    default_qty_step,
)
from openpine.gateway.deps import GatewayState, get_state
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

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


class _BacktestCancelled(RuntimeError):
    """Raised only after the owned compute process tree has stopped."""


@dataclass
class _BacktestWorker:
    process: BaseProcess
    out: object
    process_group: int | None
    start_time: int | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_requested: threading.Event = field(default_factory=threading.Event)


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


_ACTIVE_BACKTEST_WORKERS: dict[str, _BacktestWorker] = {}
_ACTIVE_BACKTEST_WORKERS_LOCK = threading.Lock()
_STARTING_BACKTEST_RUNS: set[str] = set()
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
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    closing = text.rfind(")")
    if closing < 0:
        return None
    fields = text[closing + 2 :].split()
    if len(fields) <= 19:
        return None
    try:
        return fields[0], int(fields[2]), int(fields[19])
    except ValueError:
        return None


def _enable_child_subreaper() -> None:
    """Keep escaped grandchildren owned by the backtest worker on Linux."""

    if not Path("/proc/self/stat").exists():
        return
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
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            continue
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


def _terminate_current_process_descendants(timeout: float = 2.0) -> None:
    """Fail closed until all descendants of this worker have exited."""

    root_pid = os.getpid()
    owned: dict[int, int] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        owned.update(_descendant_process_identities(root_pid))
        live = {
            pid: start_time
            for pid, start_time in owned.items()
            if (identity := _proc_identity(pid)) is not None
            and identity[0] != "Z"
            and identity[2] == start_time
        }
        if not live:
            return
        for pid, start_time in live.items():
            identity = _proc_identity(pid)
            if identity is None or identity[2] != start_time:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(0.02)

    owned.update(_descendant_process_identities(root_pid))
    for pid, start_time in owned.items():
        identity = _proc_identity(pid)
        if identity is None or identity[0] == "Z" or identity[2] != start_time:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    final_deadline = time.monotonic() + 1.0
    while time.monotonic() < final_deadline:
        live_pids = [
            pid
            for pid, start_time in owned.items()
            if (identity := _proc_identity(pid)) is not None
            and identity[0] != "Z"
            and identity[2] == start_time
        ]
        if not live_pids and not _descendant_process_identities(root_pid):
            return
        time.sleep(0.02)
    raise RuntimeError("backtest worker descendants did not stop")


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


def _process_group_has_live_members(process_group: int) -> bool:
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = stat_path.read_text(encoding="utf-8")
            closing = text.rfind(")")
            fields = text[closing + 2 :].split()
            state, pgrp = fields[0], int(fields[2])
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            continue
        if pgrp == process_group and state != "Z":
            return True
    return False


def _active_backtest_worker(run_id: str) -> _BacktestWorker | None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        return _ACTIVE_BACKTEST_WORKERS.get(run_id)


def _backtest_worker_is_starting(run_id: str) -> bool:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        return run_id in _STARTING_BACKTEST_RUNS


def _set_backtest_worker_starting(run_id: str, starting: bool) -> None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if starting:
            _STARTING_BACKTEST_RUNS.add(run_id)
        else:
            _STARTING_BACKTEST_RUNS.discard(run_id)


def _register_backtest_worker(run_id: str, worker: _BacktestWorker) -> None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if run_id in _ACTIVE_BACKTEST_WORKERS:
            raise RuntimeError(f"backtest worker already registered: {run_id}")
        _ACTIVE_BACKTEST_WORKERS[run_id] = worker


def _unregister_backtest_worker(run_id: str, worker: _BacktestWorker) -> None:
    with _ACTIVE_BACKTEST_WORKERS_LOCK:
        if _ACTIVE_BACKTEST_WORKERS.get(run_id) is worker:
            _ACTIVE_BACKTEST_WORKERS.pop(run_id, None)


def _terminate_backtest_worker(worker: _BacktestWorker, timeout: float = 3.0) -> bool:
    """Freeze, identify, and stop the complete process tree owned by a run."""

    with worker.lock:
        pid = worker.process.pid
        process_group = worker.process_group
        identity = _proc_identity(pid) if pid is not None else None
        owned: dict[int, int] = {}
        if identity is not None and pid is not None:
            _state, live_process_group, start_time = identity
            if worker.start_time is not None and start_time != worker.start_time:
                return False
            if process_group is not None and live_process_group != process_group:
                return False
            process_group = live_process_group

            try:
                os.kill(pid, signal.SIGSTOP)
            except ProcessLookupError:
                identity = None
            if identity is not None:
                verified = _proc_identity(pid)
                if verified is None or verified[2] != start_time:
                    return False
                owned[pid] = start_time
                stable_scans = 0
                while stable_scans < 3:
                    descendants = _descendant_process_identities(pid)
                    new_descendants = {
                        child_pid: child_start
                        for child_pid, child_start in descendants.items()
                        if child_pid not in owned
                    }
                    for child_pid, child_start in new_descendants.items():
                        child_identity = _proc_identity(child_pid)
                        if child_identity is None or child_identity[2] != child_start:
                            continue
                        try:
                            os.kill(child_pid, signal.SIGSTOP)
                        except ProcessLookupError:
                            continue
                        if (
                            (child_identity := _proc_identity(child_pid)) is not None
                            and child_identity[2] == child_start
                        ):
                            owned[child_pid] = child_start
                    stable_scans = stable_scans + 1 if not new_descendants else 0
                    time.sleep(0.01)

        def identity_live(owned_pid: int, owned_start: int) -> bool:
            current = _proc_identity(owned_pid)
            return (
                current is not None
                and current[0] != "Z"
                and current[2] == owned_start
            )

        def any_owned_live() -> bool:
            process_group_live = (
                process_group is not None
                and _process_group_has_live_members(process_group)
            )
            return process_group_live or any(
                identity_live(owned_pid, owned_start)
                for owned_pid, owned_start in owned.items()
            )

        if not any_owned_live():
            worker.process.join(timeout=0)
            return True

        for owned_pid, owned_start in owned.items():
            if not identity_live(owned_pid, owned_start):
                continue
            try:
                os.kill(owned_pid, signal.SIGTERM)
                os.kill(owned_pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGTERM)
                os.killpg(process_group, signal.SIGCONT)
            except ProcessLookupError:
                pass

        deadline = time.monotonic() + max(0.1, timeout * 0.67)
        while time.monotonic() < deadline and any_owned_live():
            time.sleep(0.02)
        if any_owned_live():
            for owned_pid, owned_start in owned.items():
                if not identity_live(owned_pid, owned_start):
                    continue
                try:
                    os.kill(owned_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process_group is not None:
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        final_deadline = time.monotonic() + max(0.1, timeout * 0.33)
        while time.monotonic() < final_deadline and any_owned_live():
            time.sleep(0.02)
        worker.process.join(timeout=0.1)
        return not any_owned_live()


def _parse_date_ms(value: str) -> int:
    """Parse ISO date or ms timestamp using the configured default timezone."""
    return int(parse_timestamp_ms(value, 0))


def _market_data_query_for_strategy(strategy, from_ms: int, to_ms: int):
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    return BarQuery(
        instrument=InstrumentKey(
            exchange=strategy.exchange.lower(),
            market=strategy.market_type.lower(),
            symbol=strategy.symbol.upper(),
        ),
        timeframe=parse_timeframe(strategy.timeframe),
        start_ms=from_ms,
        end_ms=to_ms,
        gap_policy="allow_with_metadata",
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


def _backtest_process_entry(
    out, adapter, strategy_class, bars, config, params, runtime_data_provider, effective_pre_bars=None
):
    if hasattr(os, "setsid") and mp.parent_process() is not None:
        try:
            os.setsid()
        except OSError as exc:
            out.put(("err", exc.__class__.__name__, "unable to isolate backtest process group", traceback.format_exc()))
            return
    _enable_child_subreaper()

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

    if hasattr(os, "setsid") and mp.parent_process() is not None:
        try:
            os.setsid()
        except OSError as exc:
            out.put(("err", exc.__class__.__name__, "unable to isolate backtest process group", traceback.format_exc()))
            return
    _enable_child_subreaper()

    def progress(done: int, total: int) -> None:
        try:
            out.put_nowait(("progress", int(done), int(total)))
        except Exception:
            pass

    try:
        from openpine.data.provider_adapter import create_local_runtime_data_provider_adapter
        from openpine.runtime.engine import BacktestEngineAdapter, load_strategy_class_from_artifact

        strategy_class = load_strategy_class_from_artifact(
            spec.pine_id,
            spec.artifact_id,
            symbol=spec.symbol,
            timeframe=spec.timeframe,
        )
        runtime_data_provider = None
        try:
            runtime_data_provider = create_local_runtime_data_provider_adapter(
                cache_dir=Path(spec.cache_dir),
                exchange=spec.exchange,
                market=spec.market,
                prefetch_end_ms=spec.prefetch_end_ms,
            )
        except Exception as exc:
            log.warning("runtime_data_provider_init_failed", error=str(exc))
        result = BacktestEngineAdapter().run(
            strategy_class,
            bars,
            config,
            params=params,
            progress_callback=progress,
            runtime_data_provider=runtime_data_provider,
        )
        _put_backtest_process_result(out, result)
    except BaseException as exc:
        _put_backtest_process_error(out, exc)


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
    proc = ctx.Process(
        target=process_target,
        args=(out, *process_args),
    )
    _set_backtest_worker_starting(run_id, True)
    try:
        proc.start()
    except BaseException:
        _set_backtest_worker_starting(run_id, False)
        out.close()
        out.cancel_join_thread()
        raise
    isolation_deadline = time.monotonic() + _BACKTEST_ISOLATION_TIMEOUT_SECONDS
    pid = getattr(proc, "pid", None)
    identity = _proc_identity(pid) if pid is not None else None
    while (
        pid is not None
        and proc.is_alive()
        and identity is not None
        and identity[1] != pid
        and time.monotonic() < isolation_deadline
    ):
        time.sleep(0.005)
        identity = _proc_identity(pid)
    if pid is not None and proc.is_alive() and (identity is None or identity[1] != pid):
        proc.terminate()
        proc.join(timeout=1.0)
        out.close()
        out.cancel_join_thread()
        _set_backtest_worker_starting(run_id, False)
        raise RuntimeError("backtest worker failed to create an isolated process group")

    verified_process_group = (
        pid
        if pid is not None
        and (
            (identity is not None and identity[1] == pid)
            or _process_group_has_live_members(pid)
        )
        else None
    )
    worker = _BacktestWorker(
        process=proc,
        out=out,
        process_group=verified_process_group,
        start_time=identity[2] if identity is not None else None,
    )
    try:
        _register_backtest_worker(run_id, worker)
    except BaseException:
        _terminate_backtest_worker(worker)
        _set_backtest_worker_starting(run_id, False)
        out.close()
        out.cancel_join_thread()
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
        if proc.is_alive():
            proc.join(timeout=1.0)
        if proc.is_alive() or (
            worker.process_group is not None
            and _process_group_has_live_members(worker.process_group)
        ):
            _terminate_backtest_worker(worker)
        else:
            proc.join(timeout=0)
        _unregister_backtest_worker(run_id, worker)
        out.close()
        out.cancel_join_thread()


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
    run_id = getattr(_BACKTEST_THREAD_CONTEXT, "run_id", None)
    cancel_requests = getattr(_BACKTEST_THREAD_CONTEXT, "cancel_requests", None)
    owned_run_id = run_id or f"internal-{time.time_ns()}"
    owned_cancel_requests = cancel_requests if cancel_requests is not None else set()
    if isinstance(adapter, _ArtifactBacktestSpec):
        return _execute_backtest_process(
            owned_run_id,
            owned_cancel_requests,
            _artifact_backtest_process_entry,
            (adapter, bars, config, params),
            progress_callback,
            "spawn",
        )
    return _execute_backtest_run_in_thread(
        owned_run_id,
        owned_cancel_requests,
        adapter,
        strategy_class,
        bars,
        config,
        params,
        runtime_data_provider,
        progress_callback,
        effective_pre_bars,
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
) -> None:
    """Execute backtest in background, update progress via WebSocket."""
    import asyncio

    async def cancel_if_requested(phase: str) -> bool:
        if run_id not in state.backtest_cancel_requests:
            return False
        state.backtest_cancel_requests.discard(run_id)
        state.backtest_store.mark_cancelled(run_id, f"Cancelled during {phase}")
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
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.0, "Strategy not found"
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

        from openpine.runtime.engine import (
            BacktestArtifactError,
            load_strategy_class_from_artifact,
        )

        try:
            load_strategy_class_from_artifact(
                strategy.pine_id,
                strategy.artifact_id,
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
            )
        except BacktestArtifactError as exc:
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

        try:
            artifact = state.artifact_store.get_artifact(
                strategy.artifact_id, strategy.pine_id
            )
            decl_args = artifact_strategy_declaration_args(artifact)
        except Exception:
            decl_args = artifact_strategy_declaration_args(None)

        params = {}
        if params_override:
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
        )

        # Reconstruct unpicklable runtime objects inside a spawned worker.  This avoids
        # forking the multithreaded API process while preserving process-tree cancellation.
        state_config = getattr(state, "config", None)
        configured_cache_root = getattr(state_config, "data_cache_root", None)
        configured_data_dir = getattr(state_config, "data_dir", None) or Path(".")
        cache_dir = (
            configured_cache_root or (configured_data_dir / "cache")
        ) / "marketdata"
        artifact_spec = _ArtifactBacktestSpec(
            pine_id=strategy.pine_id,
            artifact_id=strategy.artifact_id,
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            cache_dir=str(cache_dir),
            exchange=config.exchange,
            market=config.market_type,
            prefetch_end_ms=to_ms,
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
        state.backtest_store.mark_cancelled(run_id, message)
        ws_manager.update_progress(run_id, "backtest", "cancelled", 0.0, message)
        await ws_manager.broadcast_progress(run_id)
        state.backtest_cancel_requests.discard(run_id)
    except Exception as exc:
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
    if run.status not in {"queued", "running"}:
        return {
            "run_id": run_id,
            "action": action,
            "status": run.status,
            "accepted": False,
        }
    state.backtest_cancel_requests.add(run_id)
    worker = _active_backtest_worker(run_id)
    if worker is None and _backtest_worker_is_starting(run_id):
        import asyncio

        registration_deadline = time.monotonic() + 5.5
        while (
            worker is None
            and _backtest_worker_is_starting(run_id)
            and time.monotonic() < registration_deadline
        ):
            await asyncio.sleep(0.01)
            worker = _active_backtest_worker(run_id)
    if worker is not None:
        import asyncio

        worker.cancel_requested.set()
        stopped = await asyncio.to_thread(_terminate_backtest_worker, worker)
        if not stopped:
            raise HTTPException(503, "Backtest process tree did not stop")
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
