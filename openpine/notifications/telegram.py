"""Telegram notifier — fail-closed, token-from-env, allowlist-gated."""

from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Auto-load workspace-local OpenPine env if it exists.
_ENV_FILE = Path(".openpine/env")
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if line.startswith("export "):
                line = line[7:]
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


# ── transport protocol ────────────────────────────────────────────────────────


@runtime_checkable
class TelegramTransport(Protocol):
    """Transport protocol for Telegram sends — allows injection of fake in tests."""

    def send(
        self,
        token: str,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramSendResult:
        """Send a message via the Telegram Bot API.

        Args:
            token: Bot token from env.
            chat_id: Target chat ID.
            text: Message text.
            parse_mode: Telegram parse mode (default HTML).

        Returns:
            TelegramSendResult with success flag and optional error message.

        Raises:
            TransportError: On network/connection failure (not on Telegram API errors).
        """
        ...

    def get_updates(
        self,
        token: str,
        offset: int | None = None,
        timeout: int = 0,
        limit: int = 100,
        allowed_updates: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch updates from the Telegram Bot API."""
        ...

    def answer_callback_query(
        self,
        token: str,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> TelegramSendResult:
        """Answer an inline-keyboard callback query."""
        ...


@dataclass(frozen=True)
class TelegramSendResult:
    """Result of a Telegram send attempt."""

    ok: bool
    error_message: str | None = None


class TransportError(Exception):
    """Raised when the transport layer fails to connect or send."""


class StdlibHTTPTransport:
    """Real Telegram transport using stdlib urllib — no external deps required."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

    def _post_json(
        self, token: str, method: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = self.TELEGRAM_API.format(token=token, method=method)
        # Use JSON for reliable Unicode handling (emoji in reply_markup etc.)
        json_payload = {k: v for k, v in payload.items() if v is not None}
        body = json.dumps(json_payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read())
                data.setdefault("ok", False)
                data.setdefault("description", f"HTTP {e.code}")
                return data
            except Exception:
                return {"ok": False, "description": f"HTTP {e.code}"}
        except Exception as e:
            raise TransportError(f"Telegram transport error: {e}") from e

    def send(
        self,
        token: str,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramSendResult:
        data = self._post_json(
            token=token,
            method="sendMessage",
            payload={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            },
        )
        if not data.get("ok"):
            return TelegramSendResult(
                ok=False,
                error_message=data.get("description", "Telegram API error"),
            )
        return TelegramSendResult(ok=True)

    def get_updates(
        self,
        token: str,
        offset: int | None = None,
        timeout: int = 0,
        limit: int = 100,
        allowed_updates: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._post_json(
            token=token,
            method="getUpdates",
            payload={
                "offset": offset,
                "timeout": timeout,
                "limit": limit,
                "allowed_updates": allowed_updates,
            },
        )

    def answer_callback_query(
        self,
        token: str,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> TelegramSendResult:
        data = self._post_json(
            token=token,
            method="answerCallbackQuery",
            payload={
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            },
        )
        if not data.get("ok"):
            return TelegramSendResult(
                ok=False,
                error_message=data.get("description", "Telegram API error"),
            )
        return TelegramSendResult(ok=True)

    def get_file(self, token: str, file_id: str) -> dict[str, Any]:
        """Get file info from Telegram Bot API.

        Returns dict with 'ok', 'result' containing file_path.
        """
        return self._post_json(
            token=token, method="getFile", payload={"file_id": file_id}
        )

    def download_file(self, token: str, file_path: str) -> bytes:
        """Download file content from Telegram Bot API file path.

        Returns raw bytes of the file.
        """
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()


# ── exceptions ──────────────────────────────────────────────────────────────────


class TelegramConfigError(Exception):
    """Raised when Telegram plugin configuration is invalid or incomplete."""


class TelegramAuthorizationError(TelegramConfigError):
    """Raised when an incoming Telegram update is not authorised."""


# ── update parsing ─────────────────────────────────────────────────────────────


def _string_id(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class TelegramMessage:
    """Minimal Telegram message model used by command plugins."""

    chat_id: str | None
    text: str | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    document: dict[str, Any] | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TelegramMessage:
        chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}
        sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
        text = raw.get("text")
        if text is None:
            text = raw.get("caption")
        document = raw.get("document")
        return cls(
            chat_id=_string_id(chat.get("id")),
            text=text,
            message_id=raw.get("message_id"),
            from_user_id=_string_id(sender.get("id")),
            document=document if isinstance(document, dict) else None,
        )


@dataclass(frozen=True)
class TelegramCallbackQuery:
    """Minimal Telegram callback_query model used by inline keyboards."""

    id: str
    data: str | None = None
    chat_id: str | None = None
    message_id: int | None = None
    from_user_id: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TelegramCallbackQuery:
        message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
        return cls(
            id=str(raw.get("id", "")),
            data=raw.get("data"),
            chat_id=_string_id(chat.get("id")),
            message_id=message.get("message_id"),
            from_user_id=_string_id(sender.get("id")),
        )


@dataclass(frozen=True)
class TelegramUpdate:
    """Minimal Telegram update model for messages and callback queries."""

    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TelegramUpdate:
        message = None
        callback_query = None
        if isinstance(raw.get("message"), dict):
            message = TelegramMessage.from_api(raw["message"])
        if isinstance(raw.get("callback_query"), dict):
            callback_query = TelegramCallbackQuery.from_api(raw["callback_query"])
        return cls(
            update_id=int(raw.get("update_id", 0)),
            message=message,
            callback_query=callback_query,
        )

    @property
    def chat_id(self) -> str | None:
        if self.message:
            return self.message.chat_id
        if self.callback_query:
            return self.callback_query.chat_id
        return None

    @property
    def text(self) -> str | None:
        return self.message.text if self.message else None

    @property
    def callback_data(self) -> str | None:
        return self.callback_query.data if self.callback_query else None


# ── notifier ───────────────────────────────────────────────────────────────────


@dataclass
class TelegramPluginConfig:
    """Configuration for the Telegram notification plugin.

    Attributes:
        enabled: Whether the plugin is active. Default False (fail-closed).
        token_ref: Environment-variable reference for the bot token.
                   Format: "env:VARNAME". Never store raw tokens in config.
        chat_allowlist: List of chat IDs authorised to receive messages.
                        Empty list = no sends permitted.
    """

    enabled: bool = False
    token_ref: str = "env:OPENPINE_TELEGRAM_TOKEN"
    chat_allowlist: list[str] = field(default_factory=list)

    def resolve_token(self) -> str | None:
        """Resolve token_ref to an actual value from the environment.

        Returns:
            The environment variable value, or None if the ref is malformed/missing.

        Raises:
            ValueError: If token_ref format is not recognised.
        """
        if not self.token_ref.startswith("env:"):
            raise ValueError(
                f"token_ref must be 'env:VAR' format, got: {self.token_ref!r}"
            )
        var_name = self.token_ref[4:]
        return os.environ.get(var_name)


@dataclass(frozen=True)
class PluginInfo:
    """Runtime metadata for a loaded OpenPine plugin."""

    name: str
    plugin_type: str
    enabled: bool
    description: str = ""
    capabilities: tuple[str, ...] = ()


class PluginManager:
    """Small plugin manager used by CLI health/list commands."""

    def __init__(self, plugins: list[Any] | None = None) -> None:
        self.plugins = plugins or []

    def load_plugins(self) -> list[PluginInfo]:
        loaded: list[PluginInfo] = []
        for plugin in self.plugins:
            info = plugin.info() if callable(getattr(plugin, "info", None)) else None
            if not isinstance(info, PluginInfo):
                raise TelegramConfigError(
                    f"Plugin {plugin!r} did not provide PluginInfo metadata."
                )
            loaded.append(info)
        return loaded


class TelegramNotifier:
    """Fail-closed Telegram notifier.

    Design principles:
    - Disabled by default (enabled=False).
    - Token is always resolved from an env-ref at send-time; never stored raw.
    - chat_allowlist is always enforced: sending to an off-list chat raises.
    - dry_run=True bypasses the network call entirely — safe for smoke tests.

    Args:
        config: TelegramPluginConfig instance.
        transport: TelegramTransport instance. Defaults to StdlibHTTPTransport.
    """

    def __init__(
        self,
        config: TelegramPluginConfig | None = None,
        transport: TelegramTransport | None = None,
    ) -> None:
        self.config = config or TelegramPluginConfig()
        self.transport = transport or StdlibHTTPTransport()

    def _resolve_token(self) -> str:
        """Resolve the bot token from the configured env ref.

        Raises:
            TelegramConfigError: If no token can be resolved.
        """
        token = self.config.resolve_token()
        if not token:
            raise TelegramConfigError(
                f"Telegram token not available: {self.config.token_ref!r} "
                "is not set in the environment."
            )
        return token

    def send(
        self,
        chat_id: str,
        text: str,
        dry_run: bool = False,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramSendResult:
        """Send a message to the specified Telegram chat.

        Args:
            chat_id: Target chat ID (must be in allowlist unless allowlist is empty).
            text: Message text.
            dry_run: If True, skip network call and return ok=True immediately.

        Returns:
            TelegramSendResult indicating success or failure reason.

        Raises:
            TelegramConfigError: If plugin is disabled or token cannot be resolved.
        """
        # 1. Fail-closed: disabled plugin blocks all sends
        if not self.config.enabled:
            raise TelegramConfigError(
                "Telegram plugin is disabled. "
                "Set plugins.telegram.enabled=true to activate."
            )

        # 2. Resolve token — fail if missing
        token = self._resolve_token()

        # 3. Allowlist enforcement — empty allowlist means NO chats are permitted
        if chat_id not in self.config.chat_allowlist:
            raise TelegramConfigError(
                f"Chat {chat_id!r} is not in the allowlist. "
                f"Authorised chats: {self.config.chat_allowlist!r}. "
                "Add the chat ID to plugins.telegram.chat_allowlist to send."
            )

        # 4. Dry-run: no network
        if dry_run:
            return TelegramSendResult(ok=True)

        # 5. Real send
        return self.transport.send(
            token=token,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )

    def test(self, chat_id: str) -> TelegramSendResult:
        """Smoke-test the Telegram plugin configuration without any network call
        or token lookup.

        Enforces only the structural checks:
          - plugin must be enabled
          - target chat_id must be in the allowlist

        Does NOT resolve the token — safe to run on a machine with no token set.

        Args:
            chat_id: Chat ID to verify against the allowlist.

        Returns:
            TelegramSendResult with ok=True if enabled+allowlist pass,
            ok=False with error_message describing the failure.
        """
        if not self.config.enabled:
            return TelegramSendResult(
                ok=False,
                error_message=(
                    "Telegram plugin is disabled. "
                    "Run 'openpine plugins enable telegram' to activate."
                ),
            )

        if chat_id not in self.config.chat_allowlist:
            return TelegramSendResult(
                ok=False,
                error_message=(
                    f"Chat {chat_id!r} is not in the allowlist. "
                    f"Authorised chats: {self.config.chat_allowlist!r}. "
                    "Add it with: openpine plugins enable telegram --chat-id <id>"
                ),
            )

        return TelegramSendResult(ok=True)


class TelegramCommandPlugin:
    """Core Telegram command plugin primitives.

    This class intentionally stays below the CLI command catalog layer. It exposes
    bot transport, update parsing and fail-closed authorisation for callers that
    route commands elsewhere.
    """

    def __init__(
        self,
        config: TelegramPluginConfig | None = None,
        transport: TelegramTransport | None = None,
    ) -> None:
        self.notifier = TelegramNotifier(config=config, transport=transport)

    @property
    def config(self) -> TelegramPluginConfig:
        return self.notifier.config

    @property
    def transport(self) -> TelegramTransport:
        return self.notifier.transport

    def info(self) -> PluginInfo:
        return PluginInfo(
            name="telegram",
            plugin_type="command",
            enabled=self.config.enabled,
            description="Telegram bot command and notification plugin",
            capabilities=(
                "sendMessage",
                "reply_markup",
                "getUpdates",
                "answerCallbackQuery",
                "allowlist",
            ),
        )

    def parse_update(self, raw: dict[str, Any]) -> TelegramUpdate:
        return TelegramUpdate.from_api(raw)

    def is_chat_allowed(self, chat_id: str | None) -> bool:
        return chat_id is not None and chat_id in self.config.chat_allowlist

    def is_update_allowed(self, update: TelegramUpdate) -> bool:
        return self.config.enabled and self.is_chat_allowed(update.chat_id)

    def require_update_allowed(self, update: TelegramUpdate) -> None:
        if not self.config.enabled:
            raise TelegramAuthorizationError("Telegram plugin is disabled.")
        if not self.is_chat_allowed(update.chat_id):
            raise TelegramAuthorizationError(
                f"Incoming chat {update.chat_id!r} is not in the allowlist."
            )

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 0,
        limit: int = 100,
        allowed_updates: list[str] | None = None,
    ) -> list[TelegramUpdate]:
        token = self.notifier._resolve_token()
        data = self.transport.get_updates(
            token=token,
            offset=offset,
            timeout=timeout,
            limit=limit,
            allowed_updates=allowed_updates,
        )
        if not data.get("ok"):
            raise TransportError(data.get("description", "Telegram API error"))
        result = data.get("result", [])
        if not isinstance(result, list):
            raise TransportError("Telegram getUpdates returned non-list result")
        return [
            TelegramUpdate.from_api(item) for item in result if isinstance(item, dict)
        ]

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> TelegramSendResult:
        token = self.notifier._resolve_token()
        return self.transport.answer_callback_query(
            token=token,
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )

    def get_file(self, file_id: str) -> dict[str, Any]:
        """Get file info from Telegram Bot API."""
        token = self.notifier._resolve_token()
        return self.transport.get_file(token=token, file_id=file_id)

    def download_file(self, file_path: str) -> bytes:
        """Download file content from Telegram Bot API file path."""
        token = self.notifier._resolve_token()
        return self.transport.download_file(token=token, file_path=file_path)


# =============================================================================
# TelegramBotHandler — polling bot with async run loop and signal-safe shutdown
# =============================================================================

import asyncio
from openpine._compat import structlog
import subprocess
from types import ModuleType

logger = structlog.get_logger("telegram_bot")


# ── helpers ────────────────────────────────────────────────────────────────────


def _run_cli_argv(argv: list[str], cli_path: str = "openpine") -> str:
    """Run openpine CLI argv, return stdout. Return stderr on error."""

    try:
        result = subprocess.run(
            [cli_path] + argv,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            if result.stderr.strip():
                return f"Error:\n{result.stderr.strip()}"
            return f"Exit code: {result.returncode}"
    except subprocess.TimeoutExpired:
        return "Command timed out after 60s"
    except Exception as exc:
        return f"Command failed: {exc}"


def _format_cli_output_for_html(text: str) -> str:
    """Escape < > for HTML parse_mode, truncate at 4000 chars."""

    if not text:
        return "(no output)"
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if len(text) > 4000:
        text = text[:3997] + "..."
    return text


# ── TelegramBotHandler ─────────────────────────────────────────────────────────


class TelegramBotHandler:
    """Polling bot that fetches updates, dispatches to OpenPine CLI, sends responses."""

    def __init__(
        self,
        plugin: TelegramCommandPlugin,
        commands_module: ModuleType | None = None,
        cli_path: str = "openpine",
    ) -> None:
        self.plugin = plugin
        self.commands_module = commands_module
        self.cli_path = cli_path
        self._offset: int | None = None
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def _keyboards(self):
        """Lazy access to commands module for keyboard builders."""
        return self.commands_module

    async def run(self, poll_interval: float = 1.0) -> None:
        """Start polling loop. Runs until stop() is called or cancel signal received.
        Handles graceful shutdown on SIGTERM/SIGINT.
        """

        loop = asyncio.get_running_loop()
        self._loop = loop

        stop_event = asyncio.Event()
        self._stop_event = stop_event

        logger.info(
            "telegram_bot_starting", cli_path=self.cli_path, poll_interval=poll_interval
        )

        while not stop_event.is_set():
            try:
                processed = await asyncio.get_event_loop().run_in_executor(
                    None, self._poll_once
                )
                if processed > 0:
                    logger.debug("telegram_poll_processed", count=processed)
            except Exception as exc:
                logger.error("telegram_poll_error", error=str(exc))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass  # normal timeout — continue polling

        logger.info("telegram_bot_stopping")

    async def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._stopping = True
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        logger.info("telegram_bot_stop_requested")

    def _poll_once(self) -> int:
        """Fetch updates, process each, return number processed. Implements offset tracking."""

        try:
            updates = self.plugin.get_updates(offset=self._offset, timeout=5, limit=100)
        except Exception as exc:
            logger.warning("telegram_get_updates_error", error=str(exc))
            return 0

        processed = 0
        for update in updates:
            try:
                self._process_update(update)
                processed += 1
            except Exception as exc:
                logger.error(
                    "telegram_process_update_error",
                    update_id=update.update_id,
                    error=str(exc),
                )
            finally:
                # Advance past poison updates too; otherwise one bad update can
                # be re-polled forever and block delivery of later messages.
                next_offset = update.update_id + 1
                if self._offset is None or next_offset > self._offset:
                    self._offset = next_offset

        return processed

    def _process_update(self, update: TelegramUpdate) -> None:
        """Process one update: answer callbacks immediately, route messages to CLI."""

        # Silently ignore updates from chats not in allowlist
        if not self.plugin.is_update_allowed(update):
            chat_id = update.chat_id
            logger.debug("telegram_update_rejected_not_allowlisted", chat_id=chat_id)
            return

        # Handle callback query
        if update.callback_query is not None:
            self._handle_callback_query(update)
            return

        # Handle document (Pine script file)
        if update.message is not None and update.message.document is not None:
            self._handle_document_message(update)
            return

        # Handle message with slash command
        if (
            update.message is not None
            and update.text is not None
            and update.text.startswith("/")
        ):
            self._handle_command_message(update)

    def _handle_callback_query(self, update: TelegramUpdate) -> None:
        """Process a callback_query inline button press."""

        cq = update.callback_query
        if cq is None:
            return

        callback_data = cq.data or ""

        # Always acknowledge immediately so Telegram removes the loading spinner
        self.plugin.answer_callback_query(cq.id)

        try:
            argv = self.commands_module.map_callback_data(callback_data)
        except Exception as exc:
            # map_callback_data raises TelegramCommandError for unknown callbacks
            logger.warning(
                "telegram_callback_error", callback_data=callback_data, error=str(exc)
            )
            self.plugin.answer_callback_query(cq.id, text=str(exc), show_alert=True)
            return

        chat_id = cq.chat_id
        if chat_id is None:
            return

        if not argv:
            # No argv → menu navigation, re-render the relevant keyboard
            self._render_menu_callback(callback_data, chat_id, cq.message_id)
        else:
            # Has argv → run CLI command, send output + re-render same menu
            self._run_cli_and_respond(argv, chat_id, callback_data)

    def _render_menu_callback(
        self, callback_data: str, chat_id: str, message_id: int | None
    ) -> None:
        """Re-render and edit the message with the appropriate keyboard."""

        kb: dict[str, Any] | None = None

        if callback_data in ("op:home", "op:menu"):
            kb = self._keyboards.home_menu_keyboard()
        elif callback_data == "op:menu:data_jobs":
            kb = self._keyboards.data_jobs_keyboard()
        elif callback_data.startswith("op:reports:"):
            kb = self._keyboards.reports_keyboard()
        elif callback_data.startswith("op:risk:"):
            kb = self._keyboards.risk_keyboard()
        elif callback_data.startswith("op:strategy:"):
            # Format: op:strategy:{action}:{strategy_id}
            parts = callback_data.split(":")
            if len(parts) >= 4:
                strategy_id = parts[3]
                if strategy_id:
                    kb = self._keyboards.strategy_actions_keyboard(strategy_id)
        elif callback_data == "op:strategies:list":
            kb = self._build_strategies_list_keyboard()
        elif callback_data == "op:strat:refresh":
            kb = self._build_strategies_list_keyboard()
        elif callback_data == "op:strat:cancel_delete":
            kb = self._build_strategies_list_keyboard()
        elif callback_data.startswith("op:strat:confirm_delete:"):
            parts = callback_data.split(":")
            if len(parts) >= 4:
                strategy_id = parts[3]
                kb = self._keyboards.confirm_delete_keyboard(strategy_id)
        elif callback_data == "op:pine:list":
            kb = self._build_pine_list_keyboard()
        elif callback_data == "op:pine:refresh":
            kb = self._build_pine_list_keyboard()

        if kb is None:
            kb = self._keyboards.home_menu_keyboard()

        # Send a fresh message with the keyboard if we don't have a message_id to edit
        if message_id is not None:
            self._edit_message_reply_markup(chat_id, message_id, kb)
        else:
            self._send_message(chat_id, "Menu", reply_markup=kb)

    def _build_strategies_list_keyboard(self) -> dict[str, Any]:
        """Build strategy list keyboard by running strategy list --json."""
        import json

        try:
            output = _run_cli_argv(["strategy", "list", "--json"], self.cli_path)
            strategies = json.loads(output)
        except Exception:
            strategies = []

        return self._keyboards.strategy_list_keyboard(strategies)

    def _build_pine_list_keyboard(self) -> dict[str, Any]:
        """Build Pine source list keyboard by running pine list --json."""
        import json

        try:
            output = _run_cli_argv(["pine", "list", "--json"], self.cli_path)
            sources = json.loads(output)
        except Exception:
            sources = []

        return self._keyboards.pine_list_keyboard(sources)

    def _run_cli_and_respond(
        self, argv: list[str], chat_id: str, callback_data: str
    ) -> None:
        """Run CLI command, send result as text message + re-render the same menu."""

        output = _run_cli_argv(argv, self.cli_path)
        formatted = _format_cli_output_for_html(output)

        # Re-build same menu
        kb: dict[str, Any] | None = None
        if callback_data.startswith("op:strategy:"):
            parts = callback_data.split(":")
            if len(parts) >= 4 and parts[3]:
                kb = self._keyboards.strategy_actions_keyboard(parts[3])
        elif callback_data.startswith("op:reports:"):
            kb = self._keyboards.reports_keyboard()
        elif callback_data.startswith("op:risk:"):
            kb = self._keyboards.risk_keyboard()
        elif callback_data == "op:menu:data_jobs":
            kb = self._keyboards.data_jobs_keyboard()
        else:
            kb = self._keyboards.home_menu_keyboard()

        self._send_message(chat_id, formatted, reply_markup=kb)

    def _handle_command_message(self, update: TelegramUpdate) -> None:
        """Parse a /command message and run the corresponding CLI command."""

        msg = update.message
        if msg is None or msg.text is None:
            return

        chat_id = msg.chat_id
        if chat_id is None:
            return

        text = msg.text.strip()

        # Parse command
        try:
            argv = self.commands_module.map_telegram_command(text)
        except Exception as exc:
            logger.warning("telegram_command_error", text=text, error=str(exc))
            self._send_message(chat_id, f"Error: {exc}")
            return

        # Handle /menu and /help specially — attach home keyboard
        is_menu_or_help = text.startswith("/menu") or text.startswith("/help")

        output = _run_cli_argv(argv, self.cli_path)
        formatted = _format_cli_output_for_html(output)

        reply_markup: dict[str, Any] | None = None
        if is_menu_or_help and text.startswith("/menu"):
            reply_markup = self._keyboards.home_menu_keyboard()

        self._send_message(chat_id, formatted, reply_markup=reply_markup)

    def _handle_document_message(self, update: TelegramUpdate) -> None:
        """Handle an incoming Pine script file document.

        Downloads the file, validates extension, saves to incoming dir,
        and runs pine-add.
        """
        msg = update.message
        if msg is None or msg.document is None:
            return

        chat_id = msg.chat_id
        if chat_id is None:
            return

        doc = msg.document
        file_id = doc.get("file_id")
        if not file_id:
            self._send_message(chat_id, "Error: file has no ID.")
            return

        # Check file extension
        filename = doc.get("file_name", "")
        if not (filename.endswith(".pine") or filename.endswith(".txt")):
            self._send_message(
                chat_id,
                f"Unsupported file type: {filename!r}. Send a .pine or .txt file.",
            )
            return

        try:
            # Get file path from Telegram
            file_info = self.plugin.get_file(file_id)
            if not file_info.get("ok"):
                self._send_message(
                    chat_id, f"Failed to get file: {file_info.get('description')}"
                )
                return

            result_obj = file_info.get("result", {})
            file_path = (
                result_obj.get("file_path") if isinstance(result_obj, dict) else None
            )
            if not file_path:
                self._send_message(
                    chat_id, "Failed to retrieve file path from Telegram."
                )
                return

            # Download the file content
            content = self.plugin.download_file(file_path)

            # Save to incoming dir
            import time

            safe_name = filename.replace(" ", "_").replace("/", "_")
            incoming_dir = Path(".openpine/incoming")
            incoming_dir.mkdir(parents=True, exist_ok=True)
            dest_path = incoming_dir / f"{int(time.time())}_{safe_name}"
            dest_path.write_bytes(content)

            # Extract name from filename (without extension)
            import re

            base_name = re.sub(r"\.(pine|txt)$", "", filename, flags=re.IGNORECASE)

            # Run pine-add via CLI
            argv = ["pine", "pine-add", base_name, str(dest_path)]
            output = _run_cli_argv(argv, self.cli_path)
            formatted = _format_cli_output_for_html(output)

            # Send result with home keyboard
            self._send_message(
                chat_id,
                f"📄 File received: {filename}\n\n{formatted}",
                reply_markup=self._keyboards.home_menu_keyboard(),
            )

        except Exception as exc:
            logger.error("telegram_document_handler_error", error=str(exc))
            self._send_message(chat_id, f"Error processing file: {exc}")

    def _send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Send a message via the notifier (allowlist-gated)."""

        try:
            self.plugin.notifier.send(chat_id, text, reply_markup=reply_markup)
        except Exception as exc:
            logger.error("telegram_send_error", chat_id=chat_id, error=str(exc))

    def _edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        reply_markup: dict[str, Any],
    ) -> None:
        """Edit message reply_markup via the plugin's transport."""

        from urllib.parse import urlencode
        import json

        token = self.plugin.notifier._resolve_token()
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": json.dumps(reply_markup),
        }

        try:
            # Build URL and call editMessageReplyMarkup directly via transport
            url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
            body = urlencode(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if not result.get("ok"):
                    logger.warning(
                        "telegram_edit_reply_markup_error",
                        error=result.get("description"),
                    )
        except Exception as exc:
            logger.error("telegram_edit_reply_markup_error", error=str(exc))


__all__ = [
    "TelegramBotHandler",
    "_format_cli_output_for_html",
    "_run_cli_argv",
]
