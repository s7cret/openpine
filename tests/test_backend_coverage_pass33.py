from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from click.testing import CliRunner

from openpine.config import OpenPineConfig
from openpine.state.store import SavePolicy, StateStore, StrategyState

cli_main = importlib.import_module("openpine.cli.main")


def _config(tmp_path: Path) -> OpenPineConfig:
    data = tmp_path / "data"
    return OpenPineConfig(
        config_dir=tmp_path / "cfg",
        data_dir=data,
        sqlite_path=data / "openpine.sqlite",
        duckdb_path=data / "analytics.duckdb",
    )


def _state(strategy_id: str = "s1", bar_time: int = 10) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        artifact_id="a1",
        params_hash="p1",
        instrument_key={"symbol": "BTCUSDT", "exchange": "binance"},
        timeframe={"value": "1m"},
        state_data={"bar": bar_time},
        bar_time=bar_time,
        saved_at=bar_time,
    )


def test_state_cli_list_invalid_and_rebuild_paths(monkeypatch, tmp_path: Path):
    cfg = _config(tmp_path)
    store = StateStore(cfg.data_dir / "state")
    store.set_save_policy(SavePolicy.EVERY_BAR)
    first = store.save_snapshot(_state("s1", 10), reason="manual")
    second = store.save_snapshot(_state("s1", 20), reason="manual")
    assert first is not None and second is not None
    store.mark_invalid("s1", since_bar_time=20)

    import openpine.config as config_mod

    monkeypatch.setattr(config_mod.OpenPineConfig, "load", staticmethod(lambda: cfg))

    class FakeDataOrchestrator:
        def get_bars(self, **kwargs):
            return []

    orchestrator_mod = ModuleType("openpine.data.orchestrator")
    orchestrator_mod.DataOrchestrator = FakeDataOrchestrator
    monkeypatch.setitem(sys.modules, "openpine.data.orchestrator", orchestrator_mod)

    runner = CliRunner()
    assert runner.invoke(cli_main.cli, ["state", "list", "--strategy", "missing"]).exit_code == 0
    listed = runner.invoke(cli_main.cli, ["state", "list", "--strategy", "s1"])
    assert listed.exit_code == 0, listed.output
    assert "strategy=s1" in listed.output
    assert runner.invoke(cli_main.cli, ["state", "list"]).exit_code == 0

    # A malformed debug file exercises the defensive bar_time fallback path.
    debug_files = list((cfg.data_dir / "state" / "strategy_id=s1").glob("snap_*.debug.json"))
    assert debug_files
    debug_files[0].write_text("{broken", encoding="utf-8")
    invalid = runner.invoke(cli_main.cli, ["state", "invalid"])
    assert invalid.exit_code == 0, invalid.output
    assert "Invalid snapshots" in invalid.output

    # Rebuild without an active snapshot after invalidation exits cleanly.
    no_active = runner.invoke(cli_main.cli, ["state", "rebuild", "s1"])
    assert no_active.exit_code == 0
    assert "No active snapshots" in no_active.output

    # Rebuild success with an explicit future bar uses the real StateRebuilder but a fake data orchestrator.
    store = StateStore(cfg.data_dir / "state")
    store.save_snapshot(_state("s2", 10), reason="manual")

    ok = runner.invoke(cli_main.cli, ["state", "rebuild", "s2", "--from-bar", "20"])
    assert ok.exit_code == 0, ok.output
    assert "Rebuild successful" in ok.output


def test_state_rebuild_failure_branch(monkeypatch, tmp_path: Path):
    cfg = _config(tmp_path)
    import openpine.config as config_mod

    monkeypatch.setattr(config_mod.OpenPineConfig, "load", staticmethod(lambda: cfg))

    orchestrator_mod = ModuleType("openpine.data.orchestrator")
    orchestrator_mod.DataOrchestrator = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "openpine.data.orchestrator", orchestrator_mod)

    class FailingRebuilder:
        def __init__(self, *args, **kwargs):
            pass

        def rebuild(self, strategy_id, from_bar_time):
            from openpine.state.errors import StateInconsistencyError

            raise StateInconsistencyError("bad state")

    import openpine.recovery as recovery_mod

    monkeypatch.setattr(recovery_mod, "StateRebuilder", FailingRebuilder)
    runner = CliRunner()
    result = runner.invoke(cli_main.cli, ["state", "rebuild", "s3", "--from-bar", "1"])
    assert result.exit_code != 0
    assert "Rebuild failed" in result.output


def test_stream_setup_selected_and_cancel(monkeypatch):
    runner = CliRunner()
    assert runner.invoke(cli_main.cli, ["streams", "setup"], input="c\n").exit_code == 0
    result = runner.invoke(cli_main.cli, ["streams", "setup"], input="1\n")
    assert result.exit_code == 0
    assert "binance_ws" in result.output
