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
