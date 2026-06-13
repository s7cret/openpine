from __future__ import annotations

import json
from typing import Any

import pytest

from openpine.notifications.telegram import (
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
    TelegramUpdate,
    TransportError,
    _string_id,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.updates_payload: dict[str, Any] = {"ok": True, "result": []}
        self.answer_result = TelegramSendResult(ok=True)

    def send(
        self,
        token: str,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup=None,
    ):
        self.sent.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        return TelegramSendResult(ok=True)

    def get_updates(
        self,
        token: str,
        offset=None,
        timeout: int = 0,
        limit: int = 100,
        allowed_updates=None,
    ):
        return self.updates_payload

    def answer_callback_query(
        self, token: str, callback_query_id: str, text=None, show_alert: bool = False
    ):
        return self.answer_result

    def get_file(self, token: str, file_id: str):
        return {"ok": True, "result": {"file_id": file_id, "file_path": "docs/a.pine"}}

    def download_file(self, token: str, file_path: str):
        return b"//@version=6\n"


def test_telegram_models_parse_api_shapes():
    assert _string_id(None) is None
    assert _string_id(123) == "123"
    msg = TelegramMessage.from_api(
        {
            "chat": {"id": 1},
            "from": {"id": 2},
            "caption": "cap",
            "message_id": 3,
            "document": {"file_id": "f"},
        }
    )
    assert msg.chat_id == "1" and msg.text == "cap" and msg.from_user_id == "2"
    cb = TelegramCallbackQuery.from_api(
        {
            "id": "cb1",
            "data": "op:home",
            "from": {"id": 2},
            "message": {"chat": {"id": 1}, "message_id": 4},
        }
    )
    assert cb.chat_id == "1" and cb.message_id == 4
    update = TelegramUpdate.from_api(
        {"update_id": 9, "message": {"chat": {"id": 1}, "text": "/help"}}
    )
    assert (
        update.chat_id == "1"
        and update.text == "/help"
        and update.callback_data is None
    )
    callback_update = TelegramUpdate.from_api(
        {"update_id": 10, "callback_query": {"id": "cb", "data": "op:home"}}
    )
    assert callback_update.callback_data == "op:home"


def test_notifier_fail_closed_and_send(monkeypatch):
    transport = FakeTransport()
    disabled = TelegramNotifier(TelegramPluginConfig(enabled=False), transport)
    with pytest.raises(TelegramConfigError, match="disabled"):
        disabled.send("1", "hi")
    assert disabled.test("1").ok is False

    cfg = TelegramPluginConfig(enabled=True, chat_allowlist=["1"])
    notifier = TelegramNotifier(cfg, transport)
    with pytest.raises(TelegramConfigError, match="not available"):
        notifier.send("1", "hi")
    monkeypatch.setenv("OPENPINE_TELEGRAM_TOKEN", "token")
    with pytest.raises(TelegramConfigError, match="allowlist"):
        notifier.send("2", "hi")
    assert notifier.send("1", "dry", dry_run=True).ok is True
    assert notifier.send("1", "real").ok is True
    assert transport.sent[-1]["token"] == "token"
    assert notifier.test("1").ok is True

    with pytest.raises(ValueError):
        TelegramPluginConfig(enabled=True, token_ref="raw-token").resolve_token()


def test_plugin_manager_and_command_plugin(monkeypatch):
    monkeypatch.setenv("OPENPINE_TELEGRAM_TOKEN", "token")
    transport = FakeTransport()
    plugin = TelegramCommandPlugin(
        TelegramPluginConfig(enabled=True, chat_allowlist=["1"]), transport
    )
    info = plugin.info()
    assert info.name == "telegram" and "sendMessage" in info.capabilities
    assert PluginManager([plugin]).load_plugins()[0] == info
    with pytest.raises(TelegramConfigError):
        PluginManager([object()]).load_plugins()

    update = plugin.parse_update(
        {"update_id": 1, "message": {"chat": {"id": 1}, "text": "/help"}}
    )
    assert plugin.is_update_allowed(update) is True
    plugin.require_update_allowed(update)
    with pytest.raises(TelegramAuthorizationError):
        plugin.require_update_allowed(
            TelegramUpdate(update_id=2, message=TelegramMessage(chat_id="2"))
        )

    transport.updates_payload = {
        "ok": True,
        "result": [
            {"update_id": 3, "message": {"chat": {"id": 1}, "text": "x"}},
            "bad",
        ],
    }
    assert (
        plugin.get_updates(offset=1, timeout=2, allowed_updates=["message"])[
            0
        ].update_id
        == 3
    )
    transport.updates_payload = {"ok": False, "description": "bad"}
    with pytest.raises(TransportError, match="bad"):
        plugin.get_updates()
    transport.updates_payload = {"ok": True, "result": {}}
    with pytest.raises(TransportError, match="non-list"):
        plugin.get_updates()

    assert plugin.answer_callback_query("cb", text="ok").ok is True
    assert plugin.get_file("file1")["result"]["file_path"] == "docs/a.pine"
    assert plugin.download_file("docs/a.pine").startswith(b"//@version")


def test_stdlib_http_transport_success_api_error_and_transport_error(monkeypatch):
    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self) -> bytes:
            return self.payload

    calls: list[Any] = []

    def fake_urlopen(req, timeout=10):
        calls.append((req, timeout))
        return FakeResponse(json.dumps({"ok": True, "result": []}).encode())

    monkeypatch.setattr(
        "openpine.notifications.telegram.urllib.request.urlopen", fake_urlopen
    )
    transport = StdlibHTTPTransport()
    assert transport.send("token", "1", "hello").ok is True
    assert transport.get_updates("token")["ok"] is True
    assert transport.answer_callback_query("token", "cb").ok is True
    assert (
        transport.download_file("token", "path.bin")
        == json.dumps({"ok": True, "result": []}).encode()
    )

    def api_error(req, timeout=10):
        return FakeResponse(
            json.dumps({"ok": False, "description": "api bad"}).encode()
        )

    monkeypatch.setattr(
        "openpine.notifications.telegram.urllib.request.urlopen", api_error
    )
    assert transport.send("token", "1", "hello").error_message == "api bad"
    assert transport.answer_callback_query("token", "cb").error_message == "api bad"

    def network_error(req, timeout=10):
        raise OSError("offline")

    monkeypatch.setattr(
        "openpine.notifications.telegram.urllib.request.urlopen", network_error
    )
    with pytest.raises(TransportError, match="offline"):
        transport.get_updates("token")
