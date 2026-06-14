from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

import importlib
cli_main = importlib.import_module("openpine.cli.main")


class PineRegistry:
    sources = []
    fail_get = False
    def __init__(self):
        self.closed = False
    def list_sources(self):
        return list(type(self).sources)
    def get_source(self, name):
        if type(self).fail_get or name == "missing":
            raise KeyError(name)
        return type(self).sources[0]
    def add_source(self, text, name):
        src = SimpleNamespace(id="src", name=name, version=1, source_type="strategy", source_path="x", active_artifact_id=None, created_at=1, updated_at=2)
        type(self).sources = [src]
        return src
    def set_active_artifact(self, source_id, artifact_id):
        self.active = (source_id, artifact_id)
    def close(self):
        self.closed = True


def test_cli_pine_list_show_add_compile_paths(monkeypatch, tmp_path):
    import openpine.pine.registry as reg_mod
    monkeypatch.setattr(reg_mod, "SQLitePineSourceRegistry", PineRegistry)
    runner = CliRunner()
    PineRegistry.sources = []
    result = runner.invoke(cli_main.cli, ["pine", "list"])
    assert result.exit_code == 0 and "no sources" in result.output
    result = runner.invoke(cli_main.cli, ["pine", "list", "--json"])
    assert result.output.strip() == "[]"
    src = SimpleNamespace(id="src", name="s", version=2, source_type="indicator", active_artifact_id="art", created_at=1, updated_at=2)
    PineRegistry.sources = [src]
    assert runner.invoke(cli_main.cli, ["pine", "list"]).exit_code == 0
    assert '"name": "s"' in runner.invoke(cli_main.cli, ["pine", "list", "--json"]).output
    assert runner.invoke(cli_main.cli, ["pine", "show", "s"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "show", "missing"]).exit_code == 0
    pine_file = tmp_path / "a.pine"; pine_file.write_text("//@version=6\nplot(close)")
    assert runner.invoke(cli_main.cli, ["pine", "pine-add", "new", str(pine_file)]).exit_code == 0

    import openpine.compile as compile_mod
    monkeypatch.setattr(compile_mod, "SubprocessCompilerAdapter", lambda: object())
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": True, "artifact_id": "art2", "artifact_path": "/tmp/art"})
    assert runner.invoke(cli_main.cli, ["pine", "pine-compile", "new"]).exit_code == 0
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": False, "errors": ["bad"]})
    assert runner.invoke(cli_main.cli, ["pine", "pine-compile", "new"]).exit_code == 0
    PineRegistry.fail_get = True
    assert runner.invoke(cli_main.cli, ["pine", "pine-compile", "missing"]).exit_code == 0
    PineRegistry.fail_get = False


def test_cli_plugins_risk_events_core(monkeypatch):
    runner = CliRunner()
    class Config:
        kill_switch = False
        live_enabled = False
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=False, chat_allowlist=[], token_ref="env"))
        def save(self):
            self.saved = True
    cfg = Config()
    import openpine.config as cfg_mod
    monkeypatch.setattr(cfg_mod.OpenPineConfig, "load", classmethod(lambda cls: cfg))
    assert runner.invoke(cli_main.cli, ["risk", "show", "--show-violations"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["risk", "kill-switch", "on"]).exit_code == 0
    assert cfg.kill_switch is True
    assert runner.invoke(cli_main.cli, ["risk", "kill-switch", "on"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "bad"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "42"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "42"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "bad"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["events", "schema", "validate", "unknown"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["events", "schema", "validate", "StrategyRuntimeError"]).exit_code == 0
    import openpine.integrations as int_mod
    monkeypatch.setattr(int_mod, "check_core_libraries", lambda: [SimpleNamespace(name="x", importable=False, version=None, error="e", path="p")])
    assert runner.invoke(cli_main.cli, ["core", "check"]).exit_code != 0
