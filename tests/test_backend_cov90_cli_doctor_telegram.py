from __future__ import annotations

import subprocess
import types
import urllib.error
from types import SimpleNamespace

from click.testing import CliRunner

import importlib
cli_main = importlib.import_module("openpine.cli.main")
from openpine.notifications import telegram as tg


class Console:
    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(map(str, args)))


def test_doctor_strict_deep_and_checks(monkeypatch, tmp_path):
    class Cfg:
        data_dir = tmp_path / "data"
        config_dir = tmp_path / "cfg"
        sqlite_path = tmp_path / "db.sqlite"
        duckdb_path = tmp_path / "d.duckdb"
        kill_switch = False
        live_enabled = False
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=True, chat_allowlist=[], token_ref="env"))

    import openpine.config as cfg_mod

    monkeypatch.setattr(cfg_mod.OpenPineConfig, "load", classmethod(lambda cls: Cfg()))
    import openpine.integrations as int_mod

    statuses = [
        SimpleNamespace(name="ok", importable=True, version="1", error=None),
        SimpleNamespace(name="bad", importable=False, version=None, error="no"),
    ]
    monkeypatch.setattr(int_mod, "check_core_libraries", lambda: statuses)
    import openpine.storage as storage_mod

    class Storage:
        def __init__(self, *a, **k):
            pass

        def execute(self, sql):
            return SimpleNamespace(fetchall=lambda: [("t",)], fetchone=lambda: ("delete",))

        def close(self):
            pass

    monkeypatch.setattr(storage_mod, "SQLiteStorage", Storage)
    import openpine.data.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "DataOrchestrator", lambda: object())
    import openpine.accounts as accounts_mod

    class AM:
        def __init__(self, s):
            pass

        def list_accounts(self):
            return [1]

    monkeypatch.setattr(accounts_mod, "AccountManager", AM)
    import openpine.workers as workers_mod

    class Pool:
        JOB_TYPES = {"a"}

        def __init__(self, *a, **k):
            pass

        def get_status(self):
            return {"active_workers": 2}

    class Pool2(Pool):
        JOB_TYPES = {"b"}

    monkeypatch.setattr(workers_mod, "AggregationWorkerPool", Pool)
    monkeypatch.setattr(workers_mod, "FeatureWorkerPool", Pool2)
    import openpine.jobs as jobs_mod

    class Scheduler:
        def recover_stale_locks(self):
            return 1

        def list_jobs(self, status=None):
            return [SimpleNamespace()]

    monkeypatch.setattr(jobs_mod, "JobScheduler", Scheduler)
    import openpine.notifications as notif_mod

    class PluginManager:
        def __init__(self, *a, **k):
            pass

        def load_plugins(self):
            return [SimpleNamespace(name="telegram", plugin_type="x", enabled=True)]

    monkeypatch.setattr(notif_mod, "PluginManager", PluginManager)
    monkeypatch.setattr(notif_mod, "TelegramCommandPlugin", lambda config: object())
    import openpine.optimizer as opt_mod

    monkeypatch.setattr(
        opt_mod,
        "OptimizerService",
        lambda: SimpleNamespace(validate_config=lambda *a, **k: SimpleNamespace(status="valid")),
    )
    monkeypatch.setattr(cli_main, "__import__", __import__, raising=False)
    result = CliRunner().invoke(cli_main.cli, ["doctor", "--strict", "--deep"])
    assert result.exit_code != 0
    c = Console()
    assert cli_main._run_deep_checks(Cfg(), c, True) is False
    monkeypatch.setattr(storage_mod, "SQLiteStorage", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    assert cli_main._check_sqlite_reachable(Cfg(), c) is False
    cli_main._check_sqlite_wal_mode(Cfg(), c)

    class S2:
        def recover_stale_locks(self):
            raise RuntimeError("locks")

        def list_jobs(self, status=None):
            raise RuntimeError("jobs")

    monkeypatch.setattr(jobs_mod, "JobScheduler", S2)
    cli_main._check_job_queue_health(c)


def test_telegram_transport_and_handler_edges(monkeypatch):
    mod = tg
    tr = mod.StdlibHTTPTransport()

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b'{"ok": true, "result": []}'

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert tr.send("t", "c", "hello").ok
    assert tr.get_updates("t")["ok"] is True
    assert tr.answer_callback_query("t", "id").ok
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
    try:
        tr.get_updates("t")
    except mod.TransportError:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected TransportError")

    class HTTP(urllib.error.HTTPError):
        def __init__(self, payload):
            super().__init__("u", 400, "bad", None, None)
            self.payload = payload

        def read(self):
            return self.payload

    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(HTTP(b'{"description":"bad"}')),
    )
    assert not tr.send("t", "c", "x").ok
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(HTTP(b"bad")))
    assert not tr.send("t", "c", "x").ok

    calls = []

    class Plugin:
        def get_updates(self, **kw):
            return []

        def answer_callback_query(self, *a, **k):
            calls.append(("answer", a, k))
            return mod.TelegramSendResult(True)

        def send(self, chat_id, text, **kw):
            calls.append(("send", chat_id, text, kw))
            return mod.TelegramSendResult(True)

        @property
        def notifier(self):
            return self

        def _resolve_token(self):
            return "token"

        def get_file(self, fid):
            return {"ok": True, "result": {"file_path": "p"}}

        def download_file(self, path):
            return b"//@version=6"

    class Keyboards:
        def home_menu_keyboard(self):
            return {"home": True}

        def data_jobs_keyboard(self):
            return {"data": True}

        def reports_keyboard(self):
            return {"reports": True}

        def risk_keyboard(self):
            return {"risk": True}

        def strategy_actions_keyboard(self, sid):
            return {"sid": sid}

        def confirm_delete_keyboard(self, sid):
            return {"del": sid}

        def strategy_list_keyboard(self, xs):
            return {"strategies": xs}

        def pine_list_keyboard(self, xs):
            return {"pine": xs}

        def map_callback_to_cli(self, data):
            return [] if data == "menu" else ["version"]

        def map_telegram_command(self, text):
            if text == "/bad":
                raise RuntimeError("bad")
            return ["version"]

    h = mod.TelegramBotHandler(Plugin(), commands_module=Keyboards(), cli_path="openpine")
    monkeypatch.setattr(h, "_edit_message_reply_markup", lambda *a, **k: calls.append(("edit", a, k)))
    original_run_cli = mod._run_cli_argv
    monkeypatch.setattr(
        mod,
        "_run_cli_argv",
        lambda argv, cli_path: '[{"strategy_id":"s"}]' if "strategy" in argv else '[{"name":"p"}]',
    )
    for data in [
        "op:home",
        "op:menu:data_jobs",
        "op:reports:x",
        "op:risk:x",
        "op:strategy:start:s1",
        "op:strategies:list",
        "op:strat:refresh",
        "op:strat:cancel_delete",
        "op:strat:confirm_delete:s1",
        "op:pine:list",
        "op:pine:refresh",
        "unknown",
    ]:
        h._render_menu_callback(data, "42", 1)
    h._run_cli_and_respond(["version"], "42", "op:strategy:start:s1")
    h._run_cli_and_respond(["version"], "42", "op:reports:x")
    h._run_cli_and_respond(["version"], "42", "op:risk:x")
    h._run_cli_and_respond(["version"], "42", "op:menu:data_jobs")
    h._handle_command_message(SimpleNamespace(message=SimpleNamespace(chat_id="42", text="/menu")))
    h._handle_command_message(SimpleNamespace(message=SimpleNamespace(chat_id="42", text="/bad")))
    h._handle_command_message(SimpleNamespace(message=SimpleNamespace(chat_id=None, text="/menu")))
    h._handle_document_message(SimpleNamespace(message=SimpleNamespace(chat_id="42", document={"file_id": "id", "file_name": "a.pine"})))
    h._handle_document_message(SimpleNamespace(message=SimpleNamespace(chat_id="42", document={"file_id": "id", "file_name": "a.jpg"})))
    h._handle_document_message(SimpleNamespace(message=SimpleNamespace(chat_id="42", document={"file_name": "a.pine"})))

    class BadPlugin(Plugin):
        def get_file(self, fid):
            return {"ok": False, "description": "no"}

    mod.TelegramBotHandler(BadPlugin(), commands_module=Keyboards())._handle_document_message(
        SimpleNamespace(message=SimpleNamespace(chat_id="42", document={"file_id": "id", "file_name": "a.pine"}))
    )
    assert calls
    assert mod._format_cli_output_for_html("") == "(no output)"
    assert "&lt;" in mod._format_cli_output_for_html("<x>&")

    monkeypatch.setattr(mod, "_run_cli_argv", original_run_cli)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="err"))
    assert "Error" in mod._run_cli_argv(["bad"], "openpine")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 1)))
    assert "timed out" in mod._run_cli_argv(["bad"], "openpine")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "failed" in mod._run_cli_argv(["bad"], "openpine")
