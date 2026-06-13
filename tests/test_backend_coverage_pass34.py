from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openpine.notifications import telegram as tg


class KeyboardModule:
    @staticmethod
    def map_telegram_command(text: str) -> list[str]:
        if text == "/boom":
            raise ValueError("bad command")
        return ["config", "show"]

    @staticmethod
    def map_callback_data(data: str) -> list[str]:
        if data == "op:bad":
            raise ValueError("bad callback")
        if data == "op:menu":
            return []
        return ["strategy", "list"]

    @staticmethod
    def home_menu_keyboard() -> dict:
        return {"home": True}

    @staticmethod
    def data_jobs_keyboard() -> dict:
        return {"data": True}

    @staticmethod
    def reports_keyboard() -> dict:
        return {"reports": True}

    @staticmethod
    def risk_keyboard() -> dict:
        return {"risk": True}

    @staticmethod
    def strategy_actions_keyboard(strategy_id: str) -> dict:
        return {"strategy": strategy_id}

    @staticmethod
    def strategy_list_keyboard(strategies) -> dict:
        return {"strategies": strategies}

    @staticmethod
    def pine_list_keyboard(sources) -> dict:
        return {"pine": sources}

    @staticmethod
    def confirm_delete_keyboard(strategy_id: str) -> dict:
        return {"confirm": strategy_id}


class Plugin:
    def __init__(self):
        self.sent = []
        self.answered = []
        self.notifier = SimpleNamespace(send=self.send, _resolve_token=lambda: "TOKEN")
        self.updates = [
            tg.TelegramUpdate(
                1,
                message=tg.TelegramMessage(chat_id="1", text="/status", message_id=10),
            )
        ]

    def is_update_allowed(self, update):
        return update.chat_id == "1"

    def get_updates(self, offset=None, timeout=5, limit=100):
        if hasattr(self, "handler"):
            self.handler._loop.call_soon_threadsafe(self.handler._stop_event.set)
        out = self.updates
        self.updates = []
        return out

    def send(self, chat_id, text, parse_mode="HTML", reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return tg.TelegramSendResult(ok=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered.append((callback_query_id, text, show_alert))
        return tg.TelegramSendResult(ok=True)

    def get_file(self, file_id):
        return {"ok": True, "result": {"file_path": "scripts/a.pine"}}

    def download_file(self, file_path):
        return b"//@version=6\nindicator('x')\n"


def test_telegram_bot_handler_run_loop_and_poll_edges(monkeypatch):
    plugin = Plugin()
    handler = tg.TelegramBotHandler(plugin, commands_module=KeyboardModule, cli_path="openpine")
    plugin.handler = handler
    monkeypatch.setattr(tg, "_run_cli_argv", lambda argv, cli_path="openpine": "ok <done>")
    asyncio.run(handler.run(poll_interval=0.001))
    assert handler._offset == 2
    assert plugin.sent and "&lt;done&gt;" in plugin.sent[-1][1]

    # get_updates failures are swallowed and return zero processed updates.
    class BadPlugin(Plugin):
        def get_updates(self, *args, **kwargs):
            raise RuntimeError("network")

    bad = BadPlugin()
    bad_handler = tg.TelegramBotHandler(bad, commands_module=KeyboardModule)
    assert bad_handler._poll_once() == 0

    # Per-update processing errors do not abort the poll batch.
    handler2 = tg.TelegramBotHandler(Plugin(), commands_module=KeyboardModule)
    monkeypatch.setattr(handler2, "_process_update", lambda update: (_ for _ in ()).throw(RuntimeError("boom")))
    handler2.plugin.updates = [tg.TelegramUpdate(3, message=tg.TelegramMessage(chat_id="1", text="/status"))]
    assert handler2._poll_once() == 0


def test_telegram_handler_callback_render_and_document_edges(monkeypatch, tmp_path):
    plugin = Plugin()
    handler = tg.TelegramBotHandler(plugin, commands_module=KeyboardModule)
    monkeypatch.setattr(tg, "_run_cli_argv", lambda argv, cli_path="openpine": '[{"id": "s1"}]' if argv[:2] == ["strategy", "list"] else "ok")
    monkeypatch.chdir(tmp_path)

    handler._process_update(tg.TelegramUpdate(4, callback_query=tg.TelegramCallbackQuery(id="cb1", data="op:bad", chat_id="1", message_id=7)))
    assert plugin.answered[-1][2] is True
    handler._process_update(tg.TelegramUpdate(5, callback_query=tg.TelegramCallbackQuery(id="cb2", data="op:menu", chat_id="1", message_id=None)))
    handler._process_update(tg.TelegramUpdate(6, callback_query=tg.TelegramCallbackQuery(id="cb3", data="op:strategy:start:s1", chat_id="1")))
    assert plugin.sent

    # Document branches: missing file id, unsupported extension, bad getFile response, successful save.
    handler._process_update(tg.TelegramUpdate(7, message=tg.TelegramMessage(chat_id="1", document={"file_name": "x.pine"})))
    handler._process_update(tg.TelegramUpdate(8, message=tg.TelegramMessage(chat_id="1", document={"file_id": "f", "file_name": "x.pdf"})))
    plugin.get_file = lambda file_id: {"ok": False, "description": "nope"}
    handler._process_update(tg.TelegramUpdate(9, message=tg.TelegramMessage(chat_id="1", document={"file_id": "f", "file_name": "x.pine"})))
    plugin.get_file = lambda file_id: {"ok": True, "result": {}}
    handler._process_update(tg.TelegramUpdate(10, message=tg.TelegramMessage(chat_id="1", document={"file_id": "f", "file_name": "x.pine"})))
    plugin.get_file = lambda file_id: {"ok": True, "result": {"file_path": "x.pine"}}
    handler._process_update(tg.TelegramUpdate(11, message=tg.TelegramMessage(chat_id="1", document={"file_id": "f", "file_name": "x pine.txt"})))
    assert (tmp_path / ".openpine" / "incoming").exists()
