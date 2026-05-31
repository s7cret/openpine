"""DaemonService skeleton — section 19 of OpenPine TZ v3.

Service lifecycle management for OpenPine daemon processes.
"""

from __future__ import annotations

import asyncio
import structlog
from enum import StrEnum

log = structlog.get_logger(__name__)


class ServiceState(StrEnum):
    """Service lifecycle states — section 19."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class DaemonService:
    """Section 19: service lifecycle skeleton.

    Base class for OpenPine long-running services.
    Subclasses override _on_start and _on_stop for actual work.

    Attributes:
        name: Service name.
        state: Current ServiceState.
    """

    name: str
    state: ServiceState

    def __init__(self, name: str) -> None:
        """Initialize the daemon service.

        Args:
            name: Human-readable service name.
        """
        self.name = name
        self.state = ServiceState.STOPPED
        log.info("daemon_service.init", name=self.name, state=self.state)

    async def _on_start(self) -> None:
        """Hook: called when service starts. Override in subclass."""
        pass

    async def _on_stop(self, timeout: float) -> None:
        """Hook: called when service stops. Override in subclass."""
        pass

    async def start(self) -> None:
        """Start the service.

        Sets state to STARTING, calls _on_start, then sets state to RUNNING.
        Idempotent: calling start on an already-running service leaves state unchanged.

        Raises:
            RuntimeError: if service is not in STOPPED state.
        """
        if self.state == ServiceState.RUNNING:
            log.debug("daemon_service.start.already_running", name=self.name)
            return
        if self.state not in (ServiceState.STOPPED,):
            raise RuntimeError(
                f"Cannot start service '{self.name}' from state {self.state.value}"
            )

        self.state = ServiceState.STARTING
        log.info("daemon_service.starting", name=self.name)

        try:
            await self._on_start()
        except Exception as exc:
            self.state = ServiceState.STOPPED
            log.error("daemon_service.start.failed", name=self.name, error=str(exc))
            raise

        self.state = ServiceState.RUNNING
        log.info("daemon_service.started", name=self.name)

    async def stop(self, timeout: float = 10.0) -> None:
        """Stop the service.

        Sets state to STOPPING, calls _on_stop, then sets state to STOPPED.

        Args:
            timeout: Maximum seconds to wait for graceful shutdown.
        """
        if self.state == ServiceState.STOPPED:
            log.debug("daemon_service.stop.already_stopped", name=self.name)
            return

        self.state = ServiceState.STOPPING
        log.info("daemon_service.stopping", name=self.name, timeout=timeout)

        try:
            await asyncio.wait_for(self._on_stop(timeout), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("daemon_service.stop.timeout", name=self.name, timeout=timeout)
        except Exception as exc:
            log.error("daemon_service.stop.error", name=self.name, error=str(exc))

        self.state = ServiceState.STOPPED
        log.info("daemon_service.stopped", name=self.name)

    def is_running(self) -> bool:
        """Return True if service is in RUNNING state.

        Returns:
            True if state is RUNNING.
        """
        return self.state == ServiceState.RUNNING

    def __repr__(self) -> str:
        return f"<DaemonService {self.name} state={self.state.value}>"
