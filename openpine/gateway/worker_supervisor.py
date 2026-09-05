"""Supervise the gateway's delegated background worker process."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

ProcessFactory = Callable[[], tuple[Any, ...]]


def worker_runtime_snapshot(state: Any) -> dict[str, object]:
    """Read worker health without starting work or touching persistent storage."""

    supervisor = getattr(state, "_background_worker_supervisor", None)
    if supervisor is not None:
        return dict(supervisor.snapshot())
    process = getattr(state, "_background_worker_process", None)
    if process is not None:
        try:
            alive = bool(process.is_alive())
        except Exception:
            alive = None
        return {
            "enabled": True,
            "pid": getattr(process, "pid", None),
            "alive": alive,
            "liveness": (
                "unknown" if alive is None else "alive" if alive else "dead"
            ),
            "ready": False,
            "heartbeat_age_seconds": None,
            "heartbeat_stale": True,
            "exitcode": getattr(process, "exitcode", None),
            "last_exitcode": getattr(process, "exitcode", None),
            "restart_count": 0,
            "last_transition": None,
            "degraded": True,
            "reason": "legacy_unsupervised",
        }
    return {
        "enabled": False,
        "pid": None,
        "alive": False,
        "liveness": "dead",
        "ready": False,
        "heartbeat_age_seconds": None,
        "heartbeat_stale": False,
        "exitcode": None,
        "last_exitcode": None,
        "restart_count": 0,
        "last_transition": None,
        "degraded": False,
        "reason": "disabled",
    }


def worker_accepts_strategy_activation(state: Any) -> tuple[bool, dict[str, object]]:
    """Return whether strategy activation is safe for the configured worker mode."""

    status = worker_runtime_snapshot(state)
    if not status["enabled"]:
        return True, status
    healthy = bool(
        status.get("alive")
        and status.get("ready")
        and not status.get("heartbeat_stale")
        and not status.get("degraded")
    )
    return healthy, status


@dataclass(frozen=True)
class SupervisorConfig:
    """Bounded restart and shutdown policy for one worker process."""

    poll_interval_seconds: float = 1.0
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    max_restarts: int = 3
    restart_window_seconds: float = 300.0
    shutdown_timeout_seconds: float = 10.0
    terminate_timeout_seconds: float = 5.0
    kill_timeout_seconds: float = 2.0
    heartbeat_stale_seconds: float = 15.0
    startup_readiness_timeout_seconds: float = 30.0
    fail_safe_max_attempts: int = 3
    fail_safe_retry_seconds: float = 1.0
    fail_safe_attempt_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.backoff_initial_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("restart backoff must be non-negative")
        if self.max_restarts < 0:
            raise ValueError("max_restarts must be non-negative")
        if self.restart_window_seconds <= 0:
            raise ValueError("restart_window_seconds must be positive")
        if min(
            self.shutdown_timeout_seconds,
            self.terminate_timeout_seconds,
            self.kill_timeout_seconds,
        ) < 0:
            raise ValueError("shutdown timeouts must be non-negative")
        if self.heartbeat_stale_seconds <= 0:
            raise ValueError("heartbeat_stale_seconds must be positive")
        if self.startup_readiness_timeout_seconds <= 0:
            raise ValueError("startup_readiness_timeout_seconds must be positive")
        if self.fail_safe_max_attempts <= 0 or self.fail_safe_retry_seconds < 0:
            raise ValueError("fail-safe retry policy is invalid")
        if self.fail_safe_attempt_timeout_seconds <= 0:
            raise ValueError("fail_safe_attempt_timeout_seconds must be positive")


class WorkerSupervisor:
    """Monitor, reap and restart one process without blocking the event loop."""

    def __init__(
        self,
        process_factory: ProcessFactory,
        *,
        fail_safe: Callable[[], Any],
        config: SupervisorConfig | None = None,
        on_process: Callable[[Any], None] | None = None,
    ) -> None:
        self._process_factory = process_factory
        self._fail_safe = fail_safe
        self._config = config or SupervisorConfig()
        self._on_process = on_process
        self._process: Any | None = None
        self._stop_event: Any | None = None
        self._ready_event: Any | None = None
        self._heartbeat: Any | None = None
        self._process_started_monotonic: float | None = None
        self._start_failed_process: Any | None = None
        self._start_failed_liveness: bool | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._restart_times: list[float] = []
        self._restart_count = 0
        self._last_transition: float | None = None
        self._last_exitcode: int | None = None
        self._degraded = False
        self._reason: str | None = None
        self._fail_safe_status = "not_run"
        self._fail_safe_attempts = 0
        self._fail_safe_error: str | None = None
        self._fail_safe_task: asyncio.Task[None] | None = None

    @property
    def process(self) -> Any | None:
        return self._process

    def start(self) -> None:
        """Start the initial process and its monitor task without leaking failures."""

        if self._monitor_task is not None:
            return
        self._stopping = False
        started = self._start_process()
        self._monitor_task = asyncio.create_task(
            self._monitor(initial_start_failed=not started),
            name="openpine-background-worker-supervisor",
        )

    @property
    def fail_safe_running(self) -> bool:
        task = self._fail_safe_task
        return task is not None and not task.done()

    async def stop(self) -> bool:
        """Intentionally stop the worker and escalate until death is verified."""

        self._stopping = True
        monitor = self._monitor_task
        self._monitor_task = None
        if monitor is not None:
            monitor.cancel()
            try:
                await monitor
            except (asyncio.CancelledError, Exception):
                pass

        safe_to_close = await self._await_fail_safe_completion()

        process = self._process
        stop_event = self._stop_event
        if stop_event is not None:
            try:
                stop_event.set()
            except Exception as exc:
                self._mark_degraded("stop_event_failed", exc)
        if process is None:
            return safe_to_close

        await self._join(process, self._config.shutdown_timeout_seconds)
        liveness = self._safe_is_alive(process)
        if liveness is not False:
            try:
                process.terminate()
            except Exception as exc:
                self._mark_degraded("process_terminate_failed", exc)
            await self._join(process, self._config.terminate_timeout_seconds)
        liveness = self._safe_is_alive(process)
        if liveness is not False:
            try:
                process.kill()
            except Exception as exc:
                self._mark_degraded("process_kill_failed", exc)
            await self._join(process, self._config.kill_timeout_seconds)

        liveness = self._safe_is_alive(process)
        self._last_exitcode = self._safe_exitcode(process)
        self._last_transition = time.time()
        if liveness is not False:
            self._degraded = True
            self._reason = "shutdown_incomplete"
            safe_to_close = False
        elif safe_to_close:
            self._reason = "stopped"
        return safe_to_close

    def snapshot(self) -> dict[str, object]:
        """Return an O(1), JSON-compatible runtime health snapshot."""

        process = self._process
        alive = False if process is None else self._safe_is_alive(process)
        ready_signal = self._safe_event_is_set(self._ready_event)
        heartbeat_age: float | None = None
        heartbeat_stale = True
        if self._heartbeat is not None:
            try:
                heartbeat_value = float(self._heartbeat.value)
                if heartbeat_value > 0:
                    heartbeat_age = max(0.0, time.time() - heartbeat_value)
                heartbeat_stale = (
                    heartbeat_age is None
                    or heartbeat_age > self._config.heartbeat_stale_seconds
                )
            except Exception as exc:
                self._mark_degraded("heartbeat_status_failed", exc)
                heartbeat_stale = True
        ready = alive is True and ready_signal and not heartbeat_stale
        return {
            "enabled": True,
            "pid": None if process is None else getattr(process, "pid", None),
            "alive": alive,
            "liveness": (
                "unknown" if alive is None else "alive" if alive else "dead"
            ),
            "ready": ready,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_stale": heartbeat_stale,
            "exitcode": None if process is None else self._safe_exitcode(process),
            "last_exitcode": self._last_exitcode,
            "restart_count": self._restart_count,
            "last_transition": self._last_transition,
            "degraded": self._degraded,
            "reason": self._reason,
            "fail_safe_status": self._fail_safe_status,
            "fail_safe_attempts": self._fail_safe_attempts,
            "fail_safe_error": self._fail_safe_error,
        }

    def _start_process(self) -> bool:
        self._start_failed_process = None
        self._start_failed_liveness = None
        try:
            created = self._process_factory()
        except Exception as exc:
            self._mark_degraded("process_factory_failed", exc)
            return False
        if len(created) == 2:
            process, stop_event = created
            ready_event = None
            heartbeat = None
        elif len(created) == 4:
            process, stop_event, ready_event, heartbeat = created
        else:
            self._mark_degraded(
                "process_factory_failed",
                ValueError("process factory must return 2 or 4 values"),
            )
            return False
        self._process = process
        self._stop_event = stop_event
        self._ready_event = ready_event
        self._heartbeat = heartbeat
        try:
            process.start()
        except Exception as exc:
            self._start_failed_process = process
            self._start_failed_liveness = self._safe_is_alive(process)
            self._mark_degraded("process_start_failed", exc)
            return False
        self._process_started_monotonic = time.monotonic()
        self._degraded = True
        self._reason = "worker_starting"
        self._last_transition = time.time()
        if self._on_process is not None:
            try:
                self._on_process(process)
            except Exception as exc:
                self._mark_degraded("process_callback_failed", exc)
        log.info(
            "gateway_background_worker_started",
            pid=getattr(process, "pid", None),
            restart_count=self._restart_count,
        )
        return True

    async def _monitor(self, *, initial_start_failed: bool = False) -> None:
        if initial_start_failed:
            if (
                self._start_failed_process is not None
                and not await self._contain_start_failed_process()
            ):
                await self._ensure_terminal_fail_safe()
                return
            if not await self._restart_until_started():
                return
        while not self._stopping:
            process: Any | None = None
            try:
                await asyncio.sleep(self._config.poll_interval_seconds)
                process = self._process
                if process is None:
                    self._mark_degraded("process_missing")
                    if not await self._restart_until_started():
                        return
                    continue
                alive = self._safe_is_alive(process)
                if alive is None:
                    await self._invoke_fail_safe_safely()
                    if not await self._terminate_unhealthy_process(process):
                        return
                    # Unknown liveness is not proof of death. Never overlap it
                    # with a replacement process.
                    return
                if alive is True:
                    status = self.snapshot()
                    if status["ready"] and not status["heartbeat_stale"]:
                        if (
                            self._reason == "worker_starting"
                            and self._fail_safe_status in {"not_run", "ok"}
                        ):
                            self._degraded = False
                            self._reason = "ready"
                            self._last_transition = time.time()
                        elif (
                            self._reason is not None
                            and self._reason.endswith("_restarted")
                            and self._fail_safe_status in {"not_run", "ok"}
                        ):
                            self._degraded = False
                            self._reason = "recovered"
                            self._last_transition = time.time()
                        continue
                    if self._safe_event_is_set(self._ready_event):
                        self._degraded = True
                        self._reason = "heartbeat_stale"
                        await self._invoke_fail_safe_safely()
                        if not await self._terminate_unhealthy_process(process):
                            return
                        if not await self._wait_for_fail_safe_before_restart():
                            self._mark_degraded("heartbeat_stale_fail_safe_incomplete")
                            return
                        if not await self._restart_until_started(
                            incident_reason="heartbeat_stale",
                            fail_safe_on_exhaustion=False,
                        ):
                            return
                        continue
                    started_at = self._process_started_monotonic
                    startup_elapsed = (
                        0.0
                        if started_at is None
                        else max(0.0, time.monotonic() - started_at)
                    )
                    if (
                        startup_elapsed
                        >= self._config.startup_readiness_timeout_seconds
                    ):
                        self._degraded = True
                        self._reason = "startup_readiness_timeout"
                        await self._invoke_fail_safe_safely()
                        if not await self._terminate_unhealthy_process(process):
                            return
                        if not await self._wait_for_fail_safe_before_restart():
                            self._mark_degraded(
                                "startup_readiness_timeout_fail_safe_incomplete"
                            )
                            return
                        if not await self._restart_until_started(
                            incident_reason="startup_readiness_timeout",
                            fail_safe_on_exhaustion=False,
                        ):
                            return
                    continue

                await self._join(process, 0)
                self._last_exitcode = self._safe_exitcode(process)
                self._last_transition = time.time()
                if not await self._restart_until_started():
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_degraded("monitor_failed", exc)
                await self._invoke_fail_safe_safely()
                if process is not None and not await self._terminate_unhealthy_process(
                    process
                ):
                    self._mark_degraded("monitor_failure_shutdown_incomplete")
                return

    async def _restart_until_started(
        self,
        *,
        incident_reason: str | None = None,
        fail_safe_on_exhaustion: bool = True,
    ) -> bool:
        while not self._stopping:
            now = time.monotonic()
            cutoff = now - self._config.restart_window_seconds
            self._restart_times = [
                stamp for stamp in self._restart_times if stamp >= cutoff
            ]
            if len(self._restart_times) >= self._config.max_restarts:
                self._degraded = True
                self._reason = "restart_budget_exhausted"
                if fail_safe_on_exhaustion:
                    await self._invoke_fail_safe_safely()
                return False

            delay = min(
                self._config.backoff_initial_seconds
                * (2 ** len(self._restart_times)),
                self._config.backoff_max_seconds,
            )
            if delay:
                await asyncio.sleep(delay)
            if self._stopping:
                return False
            self._restart_times.append(time.monotonic())
            self._restart_count += 1
            if not self._start_process():
                if (
                    self._start_failed_process is not None
                    and not await self._contain_start_failed_process()
                ):
                    await self._ensure_terminal_fail_safe()
                    return False
                continue
            if incident_reason is not None:
                self._degraded = True
                self._reason = f"{incident_reason}_restarted"
            else:
                self._degraded = True
                self._reason = "worker_restarted"
            return True
        return False

    async def _contain_start_failed_process(self) -> bool:
        """Contain a start-failed candidate and allow retry only if never live."""

        process = self._start_failed_process
        if process is None:
            return True
        retry_allowed = self._start_failed_liveness is False
        contained = await self._terminate_unhealthy_process(process)
        if contained:
            self._start_failed_process = None
            self._start_failed_liveness = None
        return contained and retry_allowed

    async def _ensure_terminal_fail_safe(self) -> None:
        """Establish durable fail-closed state before a terminal monitor exit."""

        if self._fail_safe_status != "ok":
            await self._invoke_fail_safe_safely()

    async def _invoke_fail_safe_safely(self) -> None:
        running = self._fail_safe_task
        if running is not None and not running.done():
            done, _pending = await asyncio.wait(
                {running}, timeout=self._config.fail_safe_attempt_timeout_seconds
            )
            if not done:
                self._fail_safe_status = "running"
                self._fail_safe_error = "fail-safe attempt timed out and is still running"
            return
        for attempt in range(1, self._config.fail_safe_max_attempts + 1):
            self._fail_safe_attempts = attempt
            self._fail_safe_status = "running"
            try:
                task = asyncio.create_task(self._call_fail_safe())
                self._fail_safe_task = task
                task.add_done_callback(self._finish_fail_safe_task)
                done, _pending = await asyncio.wait(
                    {task}, timeout=self._config.fail_safe_attempt_timeout_seconds
                )
                if not done:
                    self._fail_safe_status = "running"
                    self._fail_safe_error = (
                        "fail-safe attempt timed out and is still running"
                    )
                    log.error(
                        "gateway_background_worker_fail_safe_timed_out",
                        attempt=attempt,
                    )
                    return
                task.result()
            except Exception as exc:
                self._fail_safe_status = "failed"
                self._fail_safe_error = str(exc)
                log.error("gateway_background_worker_fail_safe_failed", error=str(exc))
                if attempt < self._config.fail_safe_max_attempts:
                    await asyncio.sleep(self._config.fail_safe_retry_seconds)
                continue
            self._fail_safe_status = "ok"
            self._fail_safe_error = None
            return

    async def _call_fail_safe(self) -> None:
        result = await asyncio.to_thread(self._fail_safe)
        if inspect.isawaitable(result):
            await result

    async def _wait_for_fail_safe_before_restart(self) -> bool:
        """Do not overlap a replacement worker with incomplete fail-safe state."""

        task = self._fail_safe_task
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                return False
        return self._fail_safe_status == "ok"

    def _finish_fail_safe_task(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            self._fail_safe_status = "failed"
            self._fail_safe_error = "fail-safe task cancelled"
        else:
            error = task.exception()
            if error is None:
                self._fail_safe_status = "ok"
                self._fail_safe_error = None
            else:
                self._fail_safe_status = "failed"
                self._fail_safe_error = str(error)
        if self._fail_safe_task is task:
            self._fail_safe_task = None

    async def _await_fail_safe_completion(self) -> bool:
        task = self._fail_safe_task
        if task is None:
            return True
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self._config.shutdown_timeout_seconds
            )
        except TimeoutError as exc:
            self._mark_degraded("fail_safe_shutdown_incomplete", exc)
            return False
        except Exception as exc:
            self._mark_degraded("fail_safe_shutdown_failed", exc)
            return False
        return True

    async def _terminate_unhealthy_process(self, process: Any) -> bool:
        try:
            process.terminate()
        except Exception as exc:
            self._mark_degraded("process_terminate_failed", exc)
        await self._join(process, self._config.terminate_timeout_seconds)
        liveness = self._safe_is_alive(process)
        if liveness is not False:
            try:
                process.kill()
            except Exception as exc:
                self._mark_degraded("process_kill_failed", exc)
            await self._join(process, self._config.kill_timeout_seconds)
        self._last_exitcode = self._safe_exitcode(process)
        self._last_transition = time.time()
        liveness = self._safe_is_alive(process)
        if liveness is not False:
            self._degraded = True
            self._reason = "unhealthy_process_shutdown_incomplete"
            return False
        return True

    async def _join(self, process: Any, timeout: float) -> None:
        try:
            await asyncio.to_thread(process.join, timeout)
        except Exception as exc:
            self._mark_degraded("process_join_failed", exc)

    def _safe_is_alive(self, process: Any) -> bool | None:
        try:
            return bool(process.is_alive())
        except Exception as exc:
            self._mark_degraded("process_status_failed", exc)
            return None

    def _safe_event_is_set(self, event: Any | None) -> bool:
        if event is None:
            return False
        try:
            return bool(event.is_set())
        except Exception as exc:
            self._mark_degraded("ready_status_failed", exc)
            return False

    def _safe_exitcode(self, process: Any) -> int | None:
        try:
            return getattr(process, "exitcode", None)
        except Exception as exc:
            self._mark_degraded("process_status_failed", exc)
            return None

    def _mark_degraded(self, reason: str, exc: Exception | None = None) -> None:
        self._degraded = True
        self._reason = reason
        self._last_transition = time.time()
        if exc is not None:
            log.error("gateway_background_worker_supervisor_error", reason=reason, error=str(exc))
