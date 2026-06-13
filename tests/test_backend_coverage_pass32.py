from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")


class TelegramCfg:
    def __init__(self, enabled=False, allow=None, token="TOKEN"):
        self.enabled = enabled
        self.chat_allowlist = list(allow or [])
        self.token_ref = "env:OPENPINE_TELEGRAM_TOKEN"
        self._token = token
    def resolve_token(self):
        return self._token


class FakeConfig:
    def __init__(self, enabled=False, allow=None, token="TOKEN"):
        self.plugins = SimpleNamespace(telegram=TelegramCfg(enabled, allow, token))
        self.kill_switch = False
        self.live_enabled = False
        self.saved = 0
    def save(self):
        self.saved += 1


def test_provider_cli_success_error_and_http(monkeypatch):
    runner = CliRunner()
    provider_mod = ModuleType("openpine.data.provider_adapter")
    provider_mod.create_local_marketdata_provider_adapter = lambda: SimpleNamespace(_installation="local")
    monkeypatch.setitem(sys.modules, "openpine.data.provider_adapter", provider_mod)
    assert runner.invoke(cli_main.cli, ["providers", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["providers", "test", "marketdata-provider"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["providers", "test", "unknown"]).exit_code != 0

    provider_mod.create_local_marketdata_provider_adapter = lambda: None
    assert runner.invoke(cli_main.cli, ["providers", "test", "marketdata-provider"]).exit_code != 0

    requests_mod = ModuleType("requests")
    requests_mod.get = lambda url, timeout=5: SimpleNamespace(status_code=200, text="OK")
    monkeypatch.setitem(sys.modules, "requests", requests_mod)
    assert runner.invoke(cli_main.cli, ["providers", "test", "binance"]).exit_code == 0
    requests_mod.get = lambda url, timeout=5: SimpleNamespace(status_code=503, text="down")
    assert runner.invoke(cli_main.cli, ["providers", "test", "binance"]).exit_code == 0
    requests_mod.get = lambda url, timeout=5: (_ for _ in ()).throw(RuntimeError("net"))
    assert runner.invoke(cli_main.cli, ["providers", "test", "binance"]).exit_code != 0


def test_plugin_cli_enable_disable_test_and_telegram_commands(monkeypatch):
    runner = CliRunner()
    import openpine.config as cfg_mod
    config = FakeConfig(enabled=False, allow=[], token="TOKEN")
    monkeypatch.setattr(cfg_mod.OpenPineConfig, "load", staticmethod(lambda: config))

    notif_mod = ModuleType("openpine.notifications")
    class PluginManager:
        def __init__(self, plugins): self.plugins = plugins
        def load_plugins(self): return [SimpleNamespace(name="telegram", plugin_type="notifications", enabled=True)]
    class TelegramCommandPlugin:
        def __init__(self, config=None): self.config = config
    class TelegramNotifier:
        def __init__(self, config=None): self.config = config
        def test(self, chat_id): return SimpleNamespace(ok=chat_id in self.config.chat_allowlist, error_message="not allowed")
    notif_mod.PluginManager = PluginManager
    notif_mod.TelegramCommandPlugin = TelegramCommandPlugin
    notif_mod.TelegramNotifier = TelegramNotifier
    monkeypatch.setitem(sys.modules, "openpine.notifications", notif_mod)

    assert runner.invoke(cli_main.cli, ["plugins", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "bad"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "1"]).exit_code == 0
    assert config.plugins.telegram.enabled is True and "1" in config.plugins.telegram.chat_allowlist
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "test", "telegram", "--chat-id", "1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "test", "telegram", "--chat-id", "2"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "test", "bad", "--chat-id", "1"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "bad"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"]).exit_code == 0
    assert config.plugins.telegram.enabled is False
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"]).exit_code == 0

    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "commands"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "commands", "--format", "json"]).exit_code == 0
    fake_updates = '[{"update_id": 1, "message": {"text": "/status", "chat": {"id": 1}}}]'
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "poll", "--dry-run", "--fake-updates-json", fake_updates, "--once"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "webhook-info", "--dry-run"]).exit_code == 0
    config.plugins.telegram.enabled = True
    config.plugins.telegram.chat_allowlist = ["1"]
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "2", "--dry-run"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "1", "--dry-run"]).exit_code == 0


def test_telegram_token_and_api_request_edges(monkeypatch):
    cfg = FakeConfig(enabled=False, allow=["1"], token="")
    try:
        cli_main._resolve_telegram_token(cfg)
    except SystemExit:
        pass
    cfg.plugins.telegram.enabled = True
    try:
        cli_main._resolve_telegram_token(cfg)
    except SystemExit:
        pass
    cfg.plugins.telegram._token = "TOKEN"
    assert cli_main._resolve_telegram_token(cfg) == "TOKEN"

    import urllib.request as urlrequest
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"ok": true}'
    seen = []
    monkeypatch.setattr(urlrequest, "urlopen", lambda url, data=None, timeout=30: seen.append((url, data, timeout)) or Resp())
    assert cli_main._telegram_api_request("TOKEN", "method", {"x": {"nested": True}, "skip": None})["ok"] is True
    assert seen and b"nested" in seen[-1][1]
