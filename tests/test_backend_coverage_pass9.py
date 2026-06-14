from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from openpine.notifications.telegram import (
    PluginInfo,
    PluginManager,
    TelegramAuthorizationError,
    TelegramBotHandler,
    TelegramCallbackQuery,
    TelegramCommandPlugin,
    TelegramConfigError,
    TelegramMessage,
    TelegramNotifier,
    TelegramPluginConfig,
    TelegramSendResult,
    TelegramUpdate,
    TransportError,
    _format_cli_output_for_html,
    _run_cli_argv,
)


class FakeTransport:
    def __init__(self):
        self.sent: list[tuple[str, str, dict | None]] = []
        self.answered: list[tuple[str, str | None, bool]] = []
        self.updates = {"ok": True, "result": []}
        self.file_info = {"ok": True, "result": {"file_path": "scripts/x.pine"}}

    def send(self, token, chat_id, text, parse_mode="HTML", reply_markup=None):
        self.sent.append((str(chat_id), text, reply_markup))
        return TelegramSendResult(ok=True)

    def get_updates(self, token, offset=None, timeout=0, limit=100, allowed_updates=None):
        return self.updates

    def answer_callback_query(self, token, callback_query_id, text=None, show_alert=False):
        self.answered.append((callback_query_id, text, show_alert))
        return TelegramSendResult(ok=True)

    def get_file(self, token, file_id):
        return self.file_info

    def download_file(self, token, file_path):
        return b"//@version=6\nstrategy('x')\n"


class FakeKeyboards:
    @staticmethod
    def home_menu_keyboard():
        return {"inline_keyboard": [[{"text": "Home", "callback_data": "op:home"}]]}

    @staticmethod
    def data_jobs_keyboard():
        return {"inline_keyboard": [[{"text": "Data", "callback_data": "op:data"}]]}

    @staticmethod
    def reports_keyboard():
        return {"inline_keyboard": [[{"text": "Reports", "callback_data": "op:reports"}]]}

    @staticmethod
    def risk_keyboard():
        return {"inline_keyboard": [[{"text": "Risk", "callback_data": "op:risk"}]]}

    @staticmethod
    def strategy_actions_keyboard(strategy_id):
        return {"inline_keyboard": [[{"text": strategy_id, "callback_data": f"op:strategy:start:{strategy_id}"}]]}

    @staticmethod
    def strategy_list_keyboard(strategies):
        return {"inline_keyboard": [[{"text": str(len(strategies)), "callback_data": "op:strat:refresh"}]]}

    @staticmethod
    def pine_list_keyboard(sources):
        return {"inline_keyboard": [[{"text": str(len(sources)), "callback_data": "op:pine:refresh"}]]}

    @staticmethod
    def confirm_delete_keyboard(strategy_id):
        return {"inline_keyboard": [[{"text": "confirm", "callback_data": f"op:strat:delete:{strategy_id}"}]]}

    @staticmethod
    def map_callback_data(data):
        if data == "op:bad":
            raise ValueError("bad callback")
        if data == "op:run":
            return ["status"]
        return []

    @staticmethod
    def map_telegram_command(text):
        if text.startswith("/bad"):
            raise ValueError("bad command")
        if text.startswith("/menu"):
            return ["menu"]
        return ["status"]


class BadPlugin:
    pass


class GoodPlugin:
    def info(self):
        return PluginInfo("x", "command", True)


def _plugin(monkeypatch, transport: FakeTransport | None = None) -> TelegramCommandPlugin:
    monkeypatch.setenv("TOKEN", "token")
    return TelegramCommandPlugin(
        TelegramPluginConfig(enabled=True, token_ref="env:TOKEN", chat_allowlist=["42"]),
        transport or FakeTransport(),
    )


def test_telegram_models_notifier_plugin_and_transport_edges(monkeypatch):
    assert TelegramMessage.from_api({"chat": {"id": 42}, "from": {"id": 7}, "caption": "cap", "document": {"file_id": "f"}}).text == "cap"
    assert TelegramCallbackQuery.from_api({"id": "c", "data": "op:home", "from": {"id": 7}, "message": {"chat": {"id": 42}, "message_id": 5}}).chat_id == "42"
    upd = TelegramUpdate.from_api({"update_id": 1, "message": {"chat": {"id": 42}, "text": "/menu"}})
    assert upd.chat_id == "42" and upd.text == "/menu"
    cb = TelegramUpdate.from_api({"update_id": 2, "callback_query": {"id": "c", "data": "op:home", "message": {"chat": {"id": 42}}}})
    assert cb.callback_data == "op:home"
    assert TelegramUpdate(update_id=3).chat_id is None

    with pytest.raises(ValueError):
        TelegramPluginConfig(token_ref="plain-token").resolve_token()
    disabled = TelegramNotifier(TelegramPluginConfig(enabled=False), FakeTransport())
    with pytest.raises(TelegramConfigError):
        disabled.send("42", "x")
    enabled = TelegramNotifier(TelegramPluginConfig(enabled=True, token_ref="env:TOKEN", chat_allowlist=["42"]), FakeTransport())
    monkeypatch.setenv("TOKEN", "token")
    assert enabled.test("42").ok is True
    assert enabled.test("43").ok is False
    assert enabled.send("42", "hello", dry_run=True).ok is True
    with pytest.raises(TelegramConfigError):
        enabled.send("43", "blocked")
    assert PluginManager([GoodPlugin()]).load_plugins()[0].name == "x"
    with pytest.raises(TelegramConfigError):
        PluginManager([BadPlugin()]).load_plugins()

    transport = FakeTransport()
    plugin = _plugin(monkeypatch, transport)
    transport.updates = {"ok": True, "result": [{"update_id": 10, "message": {"chat": {"id": 42}, "text": "/status"}}, "bad"]}
    assert plugin.get_updates()[0].update_id == 10
    transport.updates = {"ok": False, "description": "boom"}
    with pytest.raises(TransportError):
        plugin.get_updates()
    transport.updates = {"ok": True, "result": {}}
    with pytest.raises(TransportError):
        plugin.get_updates()
    with pytest.raises(TelegramAuthorizationError):
        plugin.require_update_allowed(TelegramUpdate(update_id=9, message=TelegramMessage(chat_id="43")))
    assert plugin.answer_callback_query("cb").ok is True
    assert plugin.get_file("f")["ok"] is True
    assert plugin.download_file("x").startswith(b"//@version")


def test_telegram_cli_helpers(monkeypatch):
    assert _format_cli_output_for_html("") == "(no output)"
    assert "&lt;" in _format_cli_output_for_html("<x>&")
    assert _format_cli_output_for_html("x" * 5000).endswith("...")

    class Completed:
        def __init__(self, code=0, out="ok", err=""):
            self.returncode = code
            self.stdout = out
            self.stderr = err

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed())
    assert _run_cli_argv(["status"], "openpine") == "ok"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed(1, "", "bad"))
    assert "Error" in _run_cli_argv(["bad"], "openpine")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed(2, "", ""))
    assert "Exit code" in _run_cli_argv(["bad"], "openpine")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)
    monkeypatch.setattr(subprocess, "run", timeout)
    assert "timed out" in _run_cli_argv(["slow"], "openpine")


def test_telegram_bot_handler_callbacks_commands_documents(monkeypatch, tmp_path):
    transport = FakeTransport()
    plugin = _plugin(monkeypatch, transport)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("openpine.notifications.telegram._run_cli_argv", lambda argv, cli_path="openpine": json.dumps([{"id": "s1"}]) if "list" in argv else "done")
    handler = TelegramBotHandler(plugin, commands_module=FakeKeyboards, cli_path="openpine")

    rejected = TelegramUpdate(update_id=1, message=TelegramMessage(chat_id="99", text="/menu"))
    handler._process_update(rejected)
    assert transport.sent == []

    handler._process_update(TelegramUpdate(update_id=2, message=TelegramMessage(chat_id="42", text="/menu")))
    assert transport.sent[-1][0] == "42"
    handler._process_update(TelegramUpdate(update_id=3, message=TelegramMessage(chat_id="42", text="/bad")))
    assert "Error" in transport.sent[-1][1]
    handler._process_update(TelegramUpdate(update_id=4, callback_query=TelegramCallbackQuery(id="cb1", data="op:home", chat_id="42")))
    assert transport.answered[-1][0] == "cb1"
    handler._process_update(TelegramUpdate(update_id=5, callback_query=TelegramCallbackQuery(id="cb2", data="op:run", chat_id="42")))
    assert "done" in transport.sent[-1][1]
    handler._process_update(TelegramUpdate(update_id=6, callback_query=TelegramCallbackQuery(id="cb3", data="op:bad", chat_id="42")))
    assert transport.answered[-1][2] is True

    handler._render_menu_callback("op:menu:data_jobs", "42", None)
    handler._render_menu_callback("op:reports:daily", "42", None)
    handler._render_menu_callback("op:risk:home", "42", None)
    handler._render_menu_callback("op:strategy:start:s1", "42", None)
    handler._render_menu_callback("op:strategies:list", "42", None)
    handler._render_menu_callback("op:strat:confirm_delete:s1", "42", None)
    handler._render_menu_callback("op:pine:list", "42", None)

    handler._process_update(TelegramUpdate(update_id=7, message=TelegramMessage(chat_id="42", document={"file_id": "f", "file_name": "script.pine"})))
    assert (tmp_path / ".openpine" / "incoming").exists()
    handler._process_update(TelegramUpdate(update_id=8, message=TelegramMessage(chat_id="42", document={"file_id": "f", "file_name": "bad.exe"})))
    assert "Unsupported" in transport.sent[-1][1]
    handler._process_update(TelegramUpdate(update_id=9, message=TelegramMessage(chat_id="42", document={"file_name": "x.pine"})))
    assert "no ID" in transport.sent[-1][1]

    transport.updates = {"ok": True, "result": [{"update_id": 20, "message": {"chat": {"id": 42}, "text": "/status"}}]}
    assert handler._poll_once() == 1
    assert handler._offset == 21
    transport.updates = {"ok": False, "description": "bad"}
    assert handler._poll_once() == 0
    asyncio.run(handler.stop())
