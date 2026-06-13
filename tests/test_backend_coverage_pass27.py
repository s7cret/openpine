from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from click.testing import CliRunner

import importlib

cli_main = importlib.import_module("openpine.cli.main")


class Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *parts, **kwargs):
        self.lines.append(" ".join(str(p) for p in parts))


class Config:
    def __init__(self, tmp_path: Path):
        self.sqlite_path = tmp_path / "openpine.sqlite"
        self.duckdb_path = tmp_path / "openpine.duckdb"
        self.data_dir = tmp_path / "data"
        self.config_dir = tmp_path / "config"
        self.kill_switch = False
        self.live_enabled = False
        telegram = SimpleNamespace(
            enabled=True,
            token_ref="env:OPENPINE_TELEGRAM_TOKEN",
            chat_allowlist=["123"],
            resolve_token=lambda: "TOKEN",
        )
        self.plugins = SimpleNamespace(telegram=telegram)
        self.saved = False

    def save(self):
        self.saved = True


def test_cli_main_doctor_deep_success_and_failure_branches(monkeypatch, tmp_path: Path):
    console = Console()
    cfg = Config(tmp_path)

    class DuckConn:
        def execute(self, sql):
            return self
        def fetchone(self):
            return (1,)
        def close(self):
            pass
    duck = ModuleType("duckdb")
    duck.connect = lambda **kwargs: DuckConn()
    monkeypatch.setitem(sys.modules, "duckdb", duck)
    cli_main._check_optional_duckdb(cfg, console)
    assert any("DuckDB" in line for line in console.lines)

    duck_bad = ModuleType("duckdb")
    duck_bad.connect = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("duck boom"))
    monkeypatch.setitem(sys.modules, "duckdb", duck_bad)
    cli_main._check_optional_duckdb(cfg, console)
    assert any("duck boom" in line for line in console.lines)

    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    assert not cli_main._check_writable_dir(file_path, "bad", console)

    monkeypatch.setattr(cli_main, "_check_sqlite_reachable", lambda config, console: False)
    monkeypatch.setattr(cli_main, "_check_sqlite_wal_mode", lambda config, console: console.print("wal"))
    monkeypatch.setattr(cli_main, "_check_optional_duckdb", lambda config, console: console.print("duck"))
    monkeypatch.setattr(cli_main, "_check_job_queue_health", lambda console: console.print("jobs"))
    monkeypatch.setattr(cli_main, "_check_writable_dir", lambda path, label, console: label != "Artifact dir")

    integrations = ModuleType("openpine.integrations")
    integrations.check_core_libraries = lambda: [
        SimpleNamespace(importable=True, name="pine2ast", version="4.0", error=None),
        SimpleNamespace(importable=False, name="missing", version=None, error="nope"),
    ]
    monkeypatch.setitem(sys.modules, "openpine.integrations", integrations)

    orch = ModuleType("openpine.data.orchestrator")
    orch.DataOrchestrator = lambda: object()
    monkeypatch.setitem(sys.modules, "openpine.data.orchestrator", orch)

    accounts = ModuleType("openpine.accounts")
    accounts.AccountManager = lambda storage: SimpleNamespace(list_accounts=lambda: [1, 2])
    monkeypatch.setitem(sys.modules, "openpine.accounts", accounts)
    storage_mod = ModuleType("openpine.storage")
    storage_mod.SQLiteStorage = lambda path: SimpleNamespace(close=lambda: None)
    monkeypatch.setitem(sys.modules, "openpine.storage", storage_mod)

    workers = ModuleType("openpine.workers")
    workers.AggregationWorkerPool = lambda scheduler: SimpleNamespace(get_status=lambda: {"active_workers": 1})
    workers.FeatureWorkerPool = lambda scheduler: SimpleNamespace(get_status=lambda: {"active_workers": 2})
    monkeypatch.setitem(sys.modules, "openpine.workers", workers)
    jobs = ModuleType("openpine.jobs")
    jobs.JobScheduler = lambda: object()
    monkeypatch.setitem(sys.modules, "openpine.jobs", jobs)

    notifs = ModuleType("openpine.notifications")
    notifs.TelegramCommandPlugin = lambda config: object()
    notifs.PluginManager = lambda plugins: SimpleNamespace(load_plugins=lambda: plugins)
    monkeypatch.setitem(sys.modules, "openpine.notifications", notifs)

    ok = cli_main._run_deep_checks(cfg, console, True)
    assert ok is False
    assert any("pine2ast" in line for line in console.lines)


def test_cli_main_telegram_helpers_and_commands(monkeypatch, tmp_path: Path):
    cfg = Config(tmp_path)
    config_mod = ModuleType("openpine.config")
    config_mod.OpenPineConfig = SimpleNamespace(load=lambda: cfg)
    monkeypatch.setitem(sys.modules, "openpine.config", config_mod)

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"ok": true, "result": []}'
    calls = []
    import urllib.request as urlrequest
    monkeypatch.setattr(urlrequest, "urlopen", lambda url, data=None, timeout=30: calls.append((url, data, timeout)) or Response())
    assert cli_main._telegram_api_request("TOKEN", "getWebhookInfo")["ok"] is True
    assert cli_main._telegram_api_request("TOKEN", "sendMessage", {"chat_id": "1", "reply_markup": {"a": [1]}, "skip": None})["ok"] is True
    assert calls[1][1] is not None

    disabled = Config(tmp_path)
    disabled.plugins.telegram.enabled = False
    with pytest.raises(SystemExit):
        cli_main._resolve_telegram_token(disabled)
    no_token = Config(tmp_path)
    no_token.plugins.telegram.resolve_token = lambda: ""
    with pytest.raises(SystemExit):
        cli_main._resolve_telegram_token(no_token, require_enabled=False)

    runner = CliRunner()
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "commands", "--format", "json"])
    assert result.exit_code == 0 and "command" in result.output
    result = runner.invoke(
        cli_main.cli,
        [
            "plugins",
            "telegram",
            "poll",
            "--dry-run",
            "--once",
            "--fake-updates-json",
            json.dumps([{"update_id": 1, "message": {"text": "/status", "chat": {"id": 123}}}]),
        ],
    )
    assert result.exit_code == 0 and "Telegram updates" in result.output
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "webhook-info", "--dry-run"])
    assert result.exit_code == 0
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "123", "--dry-run"])
    assert result.exit_code == 0 and "OpenPine menu" in result.output
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "999", "--dry-run"])
    assert result.exit_code != 0


def test_cli_main_core_plugins_and_event_edge_commands(monkeypatch, tmp_path: Path):
    cfg = Config(tmp_path)
    config_mod = ModuleType("openpine.config")
    config_mod.OpenPineConfig = SimpleNamespace(load=lambda: cfg)
    monkeypatch.setitem(sys.modules, "openpine.config", config_mod)

    integrations = ModuleType("openpine.integrations")
    integrations.check_core_libraries = lambda: [
        SimpleNamespace(importable=True, name="pine2ast", version="4.0", path="/tmp/x", error=None),
        SimpleNamespace(importable=False, name="badlib", version=None, path=None, error="missing"),
    ]
    monkeypatch.setitem(sys.modules, "openpine.integrations", integrations)

    runner = CliRunner()
    assert runner.invoke(cli_main.cli, ["events", "schema", "validate", "unknown"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["events", "schema", "StrategyRuntimeError"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["core", "check"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "unknown"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "test", "unknown", "--chat-id", "123"]).exit_code != 0

    # Enable/disable telegram happy and already-disabled branches.
    result = runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "123"])
    assert result.exit_code == 0
    cfg.plugins.telegram.enabled = False
    result = runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"])
    assert result.exit_code == 0
