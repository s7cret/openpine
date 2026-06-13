"""Telegram bot daemon service — section 19 integration."""

from __future__ import annotations

import asyncio
from openpine._compat import structlog

from openpine.daemon.service import DaemonService
from openpine.notifications.telegram import (
    TelegramBotHandler,
    TelegramCommandPlugin,
)

log = structlog.get_logger(__name__)


class TelegramDaemonService(DaemonService):
    """Long-running Telegram bot service.

    Starts the TelegramBotHandler polling loop when the daemon starts,
    stops it gracefully when the daemon stops.
    """

    name = "telegram"

    def __init__(self, cli_path: str = "openpine") -> None:
        super().__init__(name=self.name)
        self._cli_path = cli_path
        self._handler: TelegramBotHandler | None = None
        self._poll_task: asyncio.Task[None] | None = None

    async def _on_start(self) -> None:
        """Start the Telegram polling loop."""
        log.info("telegram_daemon.starting")

        # Load real config
        from openpine.config import OpenPineConfig

        config = OpenPineConfig.load()
        telegram_cfg = config.plugins.telegram

        # Create the plugin with real config
        plugin = TelegramCommandPlugin(config=telegram_cfg)
        handler = TelegramBotHandler(
            plugin=plugin,
            commands_module=None,  # will be set below
            cli_path=self._cli_path,
        )
        # Import the commands module and wire it in
        from openpine import telegram_commands

        handler.commands_module = telegram_commands

        self._handler = handler

        # Start polling in background task
        self._poll_task = asyncio.create_task(self._run_poll_loop())
        log.info("telegram_daemon.started")

    async def _run_poll_loop(self) -> None:
        """Run the polling loop until shutdown."""
        if self._handler is None:
            return
        try:
            await self._handler.run(poll_interval=10.0)
        except asyncio.CancelledError:
            log.info("telegram_daemon.poll_cancelled")
            raise
        except Exception as exc:
            log.error("telegram_daemon.poll_error", error=str(exc))
            raise

    async def _on_stop(self, timeout: float) -> None:
        """Stop the Telegram polling loop."""
        log.info("telegram_daemon.stopping")
        if self._handler:
            await self._handler.stop()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await asyncio.wait_for(self._poll_task, timeout=timeout)
            except asyncio.TimeoutError:
                log.warning("telegram_daemon.stop.timeout")
            except asyncio.CancelledError:
                pass
        log.info("telegram_daemon.stopped")
