"""MarketDataRefreshService — daemon service for periodic bar refresh.

Runs PeriodicBarFetcher in background when daemon starts.
"""

from __future__ import annotations

import asyncio
from openpine._compat import structlog

from openpine.daemon.service import DaemonService
from openpine.data.periodic_fetcher import PeriodicBarFetcher, RefreshConfig

log = structlog.get_logger(__name__)


class MarketDataRefreshService(DaemonService):
    """Daemon service that refreshes bars for active strategies every minute."""

    def __init__(
        self,
        config: RefreshConfig | None = None,
    ) -> None:
        super().__init__("marketdata-refresh")
        self._fetcher = PeriodicBarFetcher(config=config)
        self._task: asyncio.Task | None = None

    async def _on_start(self) -> None:
        """Start the periodic fetcher in a background thread."""
        log.info("marketdata_refresh_service.starting")
        self._fetcher.start()
        log.info("marketdata_refresh_service.started")

    async def _on_stop(self, timeout: float) -> None:
        """Stop the periodic fetcher gracefully."""
        log.info("marketdata_refresh_service.stopping")
        self._fetcher.stop(timeout=timeout)
        log.info("marketdata_refresh_service.stopped")


__all__ = ["MarketDataRefreshService"]
