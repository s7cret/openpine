from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")


class FakeSource:
    def __init__(self, name="alpha", sid="src1", active="art1"):
        self.id = sid
        self.name = name
        self.version = 2
        self.source_type = "strategy"
        self.active_artifact_id = active
        self.created_at = 1
        self.updated_at = 2


class FakeRegistry:
    sources = [FakeSource()]
    active_set: list[tuple[str, str]] = []
    added: list[str] = []

    def __init__(self):
        self.closed = False

    def list_sources(self):
        return list(self.sources)

    def get_source(self, name):
        if name == "missing":
            raise KeyError(name)
        return FakeSource(name=name, active="art1")

    def add_source(self, source_text, name):
        self.added.append(name)
        return FakeSource(name=name, active=None)

    def set_active_artifact(self, source_id, artifact_id):
        self.active_set.append((source_id, artifact_id))

    def remove_source(self, name):
        self.removed = name

    def close(self):
        self.closed = True


class FakeArtifactStore:
    artifacts = [
        {
            "artifact_id": "art1",
            "source_id": "src1",
            "artifact_dir": "/tmp/art1",
            "python_code": "print(1)",
            "compile_meta": {"params_hash": "abcdef123456789", "saved_at": 123, "schema_version": "v"},
        },
        {"artifact_id": "art2", "compile_meta": {"params_hash": "x", "saved_at": 456}},
    ]

    def list_artifacts(self, source_id):
        return list(self.artifacts)


def _install_fake_modules(monkeypatch, *, compile_success=True):
    registry_mod = ModuleType("openpine.pine.registry")
    registry_mod.SQLitePineSourceRegistry = FakeRegistry
    artifact_mod = ModuleType("openpine.artifacts")
    artifact_mod.ArtifactStore = FakeArtifactStore
    compile_mod = ModuleType("openpine.compile")

    class Adapter:
        pass

    def compile_pipeline(source, adapter):
        if compile_success:
            return {"success": True, "artifact_id": "art2", "artifact_path": "/tmp/art2"}
        return {"success": False, "errors": ["boom"]}

    compile_mod.SubprocessCompilerAdapter = Adapter
    compile_mod.compile_pipeline = compile_pipeline
    monkeypatch.setitem(sys.modules, "openpine.pine.registry", registry_mod)
    monkeypatch.setitem(sys.modules, "openpine.artifacts", artifact_mod)
    monkeypatch.setitem(sys.modules, "openpine.compile", compile_mod)


def test_pine_cli_source_and_artifact_commands(monkeypatch, tmp_path: Path):
    _install_fake_modules(monkeypatch)
    runner = CliRunner()
    source_file = tmp_path / "a.pine"
    source_file.write_text("//@version=6\nstrategy('x')\n", encoding="utf-8")

    assert runner.invoke(cli_main.cli, ["pine", "list"]).exit_code == 0
    result = runner.invoke(cli_main.cli, ["pine", "list", "--json"])
    assert result.exit_code == 0 and '"alpha"' in result.output
    assert runner.invoke(cli_main.cli, ["pine", "show", "alpha"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "show", "missing"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "pine-add", "alpha", str(source_file)]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "pine-compile", "alpha"]).exit_code == 0
    assert FakeRegistry.active_set[-1] == ("src1", "art2")
    assert runner.invoke(cli_main.cli, ["pine", "artifacts", "alpha"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "artifacts", "missing"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "inspect", "alpha"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "inspect", "missing"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "rollback", "alpha"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "rollback", "alpha", "--to-version", "bad"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "rollback", "alpha", "--to-version", "art2"]).exit_code == 0
    assert FakeRegistry.active_set[-1] == ("src1", "art2")


def test_pine_cli_compile_failure_and_no_artifacts(monkeypatch):
    _install_fake_modules(monkeypatch, compile_success=False)
    runner = CliRunner()
    result = runner.invoke(cli_main.cli, ["pine", "pine-compile", "alpha"])
    assert result.exit_code == 0 and "Compile failed" in result.output

    class EmptyStore(FakeArtifactStore):
        artifacts = []

    artifact_mod = ModuleType("openpine.artifacts")
    artifact_mod.ArtifactStore = EmptyStore
    monkeypatch.setitem(sys.modules, "openpine.artifacts", artifact_mod)
    assert "no artifacts" in runner.invoke(cli_main.cli, ["pine", "artifacts", "alpha"]).output
    assert "no artifacts" in runner.invoke(cli_main.cli, ["pine", "inspect", "alpha"]).output
    assert "No artifacts" in runner.invoke(cli_main.cli, ["pine", "rollback", "alpha"]).output


def test_pine_cli_versions_activate_remove_and_streams(monkeypatch, tmp_path: Path):
    _install_fake_modules(monkeypatch)
    runner = CliRunner()
    artifact_dir = tmp_path / "artifact_dir"
    artifact_dir.mkdir()
    (artifact_dir / "x.txt").write_text("x", encoding="utf-8")
    FakeArtifactStore.artifacts = [
        {"artifact_id": "art1", "artifact_dir": str(artifact_dir), "compile_meta": {"created_at": 10}},
        {"artifact_id": "art2", "artifact_dir": "", "compile_meta": {"created_at": 20}},
    ]
    assert runner.invoke(cli_main.cli, ["pine", "versions", "alpha"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "versions", "missing"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "activate", "alpha", "bad"]).exit_code == 0
    result = runner.invoke(cli_main.cli, ["pine", "activate", "alpha", "art2"])
    assert result.exit_code == 0 and FakeRegistry.active_set[-1] == ("src1", "art2")
    assert runner.invoke(cli_main.cli, ["pine", "activate", "missing", "art1"]).exit_code == 0
    result = runner.invoke(cli_main.cli, ["pine", "remove", "alpha"])
    assert result.exit_code == 0 and not artifact_dir.exists()
    assert runner.invoke(cli_main.cli, ["pine", "remove", "missing"]).exit_code == 0

    assert runner.invoke(cli_main.cli, ["streams", "plan"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["streams", "setup"], input="c\n").exit_code == 0
    result = runner.invoke(cli_main.cli, ["streams", "setup"], input="1\n")
    assert result.exit_code == 0 and "binance_ws" in result.output
    assert runner.invoke(cli_main.cli, ["version"]).exit_code == 0


def test_doctor_strict_branches(monkeypatch):
    runner = CliRunner()
    # Keep strict path cheap and deterministic.
    structlog_mod = ModuleType("structlog")
    structlog_mod.get_logger = lambda *args, **kwargs: SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None, debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "structlog", structlog_mod)
    monkeypatch.setattr(cli_main, "_validate_event_schema", lambda event_type: True)
    monkeypatch.setattr(cli_main, "_run_deep_checks", lambda config, console, all_ok: all_ok)
    result = runner.invoke(cli_main.cli, ["doctor", "--strict"])
    assert result.exit_code == 0

    monkeypatch.setattr(cli_main, "_validate_event_schema", lambda event_type: False)
    result = runner.invoke(cli_main.cli, ["doctor", "--strict"])
    assert result.exit_code != 0


def test_validate_event_schema_unknown():
    assert cli_main._validate_event_schema("unknown_event") is False
