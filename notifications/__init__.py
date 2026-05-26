"""OpenPine notifications package — Telegram notifier plugin."""

from openpine.notifications.telegram import (
    PluginInfo,
    PluginManager,
    StdlibHTTPTransport,
    TelegramAuthorizationError,
    TelegramCallbackQuery,
    TelegramCommandPlugin,
    TelegramConfigError,
    TelegramMessage,
    TelegramNotifier,
    TelegramPluginConfig,
    TelegramSendResult,
    TelegramTransport,
    TelegramUpdate,
    TransportError,
)

__all__ = [
    "PluginInfo",
    "PluginManager",
    "TelegramAuthorizationError",
    "TelegramCallbackQuery",
    "TelegramCommandPlugin",
    "TelegramConfigError",
    "TelegramMessage",
    "TelegramNotifier",
    "TelegramPluginConfig",
    "TelegramSendResult",
    "TelegramTransport",
    "TelegramUpdate",
    "StdlibHTTPTransport",
    "TransportError",
]
