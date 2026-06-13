from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")
from openpine.batch import runner as batch_runner
from openpine.batch.tv_corpus import ChartExport, ExportEntry
from openpine.gateway.routes import backtest as backtest_routes


def _pine_file(tmp_path: Path, text: str, name: str = "001_demo.pine") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_run_indicator_and_strategy_branches(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []
    def fake_run(args: list[str]) -> str:
        calls.append(list(args))
        if args[:2] == ["strategy", "create"]:
            return "Strategy created: strat_123\n"
        return "ok\n"
    monkeypatch.setattr(cli_main, "_run_openpine_cli", fake_run)
    runner = CliRunner()
    indicator = _pine_file(tmp_path, '//@version=6\nindicator("i")\nplot(close)\n')
    result = runner.invoke(cli_main.cli, ["run", str(indicator), "--symbol", "BTCUSDT", "--timeframe", "15m", "--from", "2026-01-01", "--to", "2026-01-02", "--compare-from", "2026-01-01", "--compare-to", "2026-01-02", "--output", str(tmp_path / "out_i"), "--tv-chart", str(tmp_path / "tv.csv")])
    assert result.exit_code == 0, result.output
    assert any(call[:2] == ["pine", "pine-add"] for call in calls)
    assert any(call[:2] == ["pine", "run-plots"] for call in calls)
    assert any(call[:2] == ["pine", "compare-tv"] for call in calls)
    calls.clear()
    strategy = _pine_file(tmp_path, '//@version=6\nstrategy("s")\n', "strategy.pine")
    result = runner.invoke(cli_main.cli, ["run", str(strategy), "--symbol", "ETHUSDT", "--timeframe", "1h", "--from", "2026-01-01", "--history-from", "2025-12-01", "--compare-from", "2026-01-01", "--compare-to", "2026-01-02", "--output", str(tmp_path / "out_s"), "--tv-chart", str(tmp_path / "tv_chart.csv"), "--tv-trades", str(tmp_path / "tv_trades.csv"), "--tv-equity", str(tmp_path / "tv_equity.csv"), "--capture-plots"])
    assert result.exit_code == 0, result.output
    assert any(call[:2] == ["strategy", "create"] for call in calls)
    assert any(call[:2] == ["strategy", "backtest"] for call in calls)
    assert any(call[:2] == ["strategy", "compare-tv"] for call in calls)


def test_cli_run_failure_and_detection_helpers(monkeypatch, tmp_path: Path):
    assert cli_main._auto_pine_source_name(Path("001_alpha_beta.pine")) == "po_0001_001_alpha_beta"
    assert cli_main._auto_pine_source_name(Path("custom.pine")) == "po_custom"
    assert cli_main._detect_pine_source_kind(_pine_file(tmp_path, 'study("x")\n', "study.pine")) == "indicator"
    with pytest.raises(Exception):
        cli_main._detect_pine_source_kind(_pine_file(tmp_path, "close\n", "bad.pine"))
    def failing(args: list[str]) -> str:
        raise cli_main.click.ClickException("boom")
    monkeypatch.setattr(cli_main, "_run_openpine_cli", failing)
    result = CliRunner().invoke(cli_main.cli, ["run", str(_pine_file(tmp_path, 'indicator("i")\n', "i.pine")), "--symbol", "BTCUSDT", "--timeframe", "15m", "--from", "2026-01-01", "--output", str(tmp_path / "out")])
    assert result.exit_code != 0


def test_doctor_helper_edges(tmp_path: Path):
    assert cli_main._validate_event_schema("unknown") is False
    assert cli_main._check_writable_dir(tmp_path / "w", "Writable", cli_main.console) is True
    bad = tmp_path / "already_a_file"
    bad.write_text("x", encoding="utf-8")
    assert cli_main._check_writable_dir(bad, "Bad", cli_main.console) is False
    cli_main._print_state_policy()
    class Config:
        sqlite_path = tmp_path / "doctor.sqlite"
        duckdb_path = tmp_path / "missing.duckdb"
        data_dir = tmp_path / "data"
        config_dir = tmp_path / "cfg"
        kill_switch = False
        live_enabled = False
    assert cli_main._check_sqlite_reachable(Config, cli_main.console) is True
    cli_main._check_sqlite_wal_mode(Config, cli_main.console)
    cli_main._check_optional_duckdb(Config, cli_main.console)


def test_batch_metadata_completion_and_offsets(tmp_path: Path):
    root = tmp_path / "entry"; root.mkdir()
    pine = root / "source.pine"; pine.write_text("strategy('s')", encoding="utf-8")
    chart_path = root / "chart_15m.csv"
    pd.DataFrame({"time": [1_700_000_000, 1_700_000_900, 1_700_001_800, 1_700_002_700], "open": [1, 2, 3, 4], "high": [2, 3, 4, 5], "low": [0, 1, 2, 3], "close": [1.5, 2.5, 3.5, 4.5], "Volume": [10, 20, 30, 40], "BAR_INDEX": [100, 101, 102, 103]}).to_csv(chart_path, index=False)
    chart = ChartExport("15m", chart_path, 4, 1_700_000_000_000, 1_700_002_700_000)
    entry = ExportEntry(7, "folder", "strategy", "group", root, pine, (chart,))
    assert batch_runner._expected_output_files(entry, chart)[1].name == "trades.csv"
    assert batch_runner._wanted_charts(entry, argparse.Namespace(timeframe="15")) == [chart]
    valid = root / "openpine_outputs" / "15m" / "trades.csv"; valid.parent.mkdir(parents=True); valid.write_text("x", encoding="utf-8")
    assert batch_runner._output_file_valid(valid) is True
    assert batch_runner._valid_window({"from_ms": 1, "to_ms": 2}) is True
    assert batch_runner._valid_window({"from_ms": 2, "to_ms": 1}) is False
    assert batch_runner._valid_window([]) is False
    libs = {name: "rev" for name in batch_runner.LIBRARY_NAMES}
    run_info = {"status": "ok", "data": {"calculation_from": 1, "calculation_to": 3, "compare_from": 1, "compare_to": 2}, "bars": 4}
    meta = batch_runner._build_run_meta(entry=entry, chart=chart, status={"source_id": "src", "artifact_id": "art"}, run_info=run_info, batch_id="b", library_revisions=libs)
    summary = batch_runner._build_run_summary(entry=entry, chart=chart, run_meta=meta, run_info=run_info)
    assert meta["run_id"] == "b_0007_15m" and summary["status"] == "ok"
    out_dir = root / "openpine_outputs" / "15m"
    for path in batch_runner._expected_output_files(entry, chart):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text("x", encoding="utf-8")
    batch_runner.write_json(out_dir / "run_meta.json", meta)
    batch_runner.write_json(out_dir / "summary.json", {"schema_version": batch_runner.RUN_META_SCHEMA_VERSION, "status": "ok"})
    batch_runner.write_json(root / "openpine_outputs" / "openpine_batch_status.json", {"phase": "run", "status": "ok", "runs": [{"timeframe": "15m", "status": "ok"}]})
    assert batch_runner.completed_for_selection(entry, argparse.Namespace(skip_completed=True, phase="run", timeframe=None)) is True
    offset, align = batch_runner._infer_tv_bar_index_offset(chart, [SimpleNamespace(time=1_700_000_000_000 + i * 900_000) for i in range(4)])
    assert offset == 100 and align and align["status"] == "inferred"
    off, meta2 = batch_runner._infer_tv_bar_index_offset_from_periodic_na(pd.DataFrame({"A": [None, 1, 1, None, 1, 1, None], "B": [1, None, 1, 1, None, 1, 1]}), 5)
    assert isinstance(off, int)


def test_batch_registry_compile_and_progress_edges(monkeypatch, tmp_path: Path, capsys):
    batch_runner._write_progress(tmp_path / "progress", "b", 1, "run", "ok", "note", 2, 1, {"15m": {"ok": 1}})
    assert json.loads((tmp_path / "progress" / "current_progress.json").read_text())["summary_by_timeframe"]["15m"]["ok"] == 1
    cb = batch_runner.build_progress_callback("label", 2); assert cb is not None
    cb(1, 10); cb(3, 10); cb(10, 10); assert "runtime label" in capsys.readouterr().out
    assert batch_runner.build_progress_callback("label", 0) is None
    entry = SimpleNamespace(export_id=3, folder="f", kind="strategy", pine_path=tmp_path / "s.pine"); entry.pine_path.write_text("strategy('s')", encoding="utf-8")
    class SourceRegistry:
        def __init__(self): self._conn = SimpleNamespace(execute=lambda *a, **k: None, commit=lambda: None)
        def get_source(self, name): raise KeyError(name)
        def add_source(self, text, name): return SimpleNamespace(id="src", source_type=None, source_path=None)
        def close(self): pass
    monkeypatch.setattr(batch_runner, "load_source_registry", SourceRegistry)
    source, created = batch_runner.get_or_add_source(entry, write=True)
    assert created and source.id == "src"
    assert batch_runner.get_or_add_source(entry, write=False) == (None, False)
    class StrategyRegistry:
        def list_strategies(self): return []
        def register_strategy(self, **kwargs): return SimpleNamespace(strategy_id="sid")
        def update_status(self, sid, status): self.updated = (sid, status)
        def close(self): pass
    monkeypatch.setattr(batch_runner, "load_strategy_registry", StrategyRegistry)
    assert batch_runner.ensure_strategy_instance(entry, SimpleNamespace(id="src"), "art", "15m") == ("sid", True)


def test_backtest_route_helpers_with_fake_storage(tmp_path: Path):
    assert backtest_routes._normalize_metrics_payload({"metrics": {"a": 1}, "nested": {"b": 2}}) == {"a": 1}
    assert backtest_routes._normalize_metrics_payload(None) is None
    assert backtest_routes._parse_date_ms("1234") == 1234000
    class Query:
        instrument = SimpleNamespace(exchange="binance", market="spot", symbol="BTCUSDT"); timeframe = SimpleNamespace(canonical="15m"); start_ms = 1; end_ms = 3
    series = SimpleNamespace(query=Query(), bars=[SimpleNamespace(time=1, time_close=2, open=1, high=2, low=0, close=1.5, volume=10)])
    assert len(backtest_routes._bar_series_fingerprint(series)) == 64
    import sqlite3
    conn = sqlite3.connect(tmp_path / "bt.sqlite")
    conn.execute("CREATE TABLE backtest_runs(run_id TEXT PRIMARY KEY, updated_at INTEGER)"); conn.execute("INSERT INTO backtest_runs(run_id, updated_at) VALUES('r1', 0)"); conn.commit()
    backtest_routes._save_backtest_data_fingerprint(SimpleNamespace(storage=conn), "r1", "abc")
    assert conn.execute("SELECT data_fingerprint FROM backtest_runs WHERE run_id='r1'").fetchone()[0] == "abc"; conn.close()
    class Out:
        def __init__(self): self.items = []
        def put_nowait(self, item): self.items.append(item)
        def put(self, item): self.items.append(item)
    class Adapter:
        def run(self, *args, progress_callback=None, **kwargs): progress_callback(1, 2); return "ok"
    out = Out(); backtest_routes._backtest_process_entry(out, Adapter(), object, [], {}, {}, None)
    assert out.items[0][0] == "progress" and out.items[-1] == ("ok", "ok")
    class BadAdapter:
        def run(self, *args, **kwargs): raise ValueError("bad")
    out = Out(); backtest_routes._backtest_process_entry(out, BadAdapter(), object, [], {}, {}, None)
    assert out.items[-1][0] == "err"


def test_pine_cli_artifact_lifecycle_with_fakes(monkeypatch, tmp_path: Path):
    import openpine.artifacts as artifacts_mod
    import openpine.compile as compile_mod
    import openpine.pine.registry as pine_registry_mod
    source = SimpleNamespace(id="pine1", name="demo", version=2, source_type="strategy", active_artifact_id="art1", created_at=1, updated_at=2)
    artifact_dir = tmp_path / "art1"; artifact_dir.mkdir()
    artifacts = [{"artifact_id": "art1", "source_id": "pine1", "artifact_dir": str(artifact_dir), "python_code": "print('x')", "compile_meta": {"params_hash": "abcdef1234567890", "saved_at": "now", "created_at": 123, "schema_version": "v"}}, {"artifact_id": "art2", "artifact_dir": "", "compile_meta": {}}]
    removed: list[str] = []; active: list[tuple[str, str]] = []
    class FakePineRegistry:
        def list_sources(self): return [source]
        def get_source(self, name):
            if name == "missing": raise KeyError(name)
            return source
        def add_source(self, text, name): return SimpleNamespace(id="new", name=name)
        def set_active_artifact(self, source_id, artifact_id): active.append((source_id, artifact_id))
        def remove_source(self, name): removed.append(name)
        def close(self): pass
    class FakeStore:
        def list_artifacts(self, source_id): return list(artifacts)
    monkeypatch.setattr(pine_registry_mod, "SQLitePineSourceRegistry", FakePineRegistry)
    monkeypatch.setattr(artifacts_mod, "ArtifactStore", FakeStore)
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": True, "artifact_id": "art3", "artifact_path": "/tmp/art3"})
    runner = CliRunner(); src = tmp_path / "source.pine"; src.write_text("strategy('s')", encoding="utf-8")
    for args in (["pine", "list"], ["pine", "list", "--json"], ["pine", "show", "demo"], ["pine", "pine-add", "demo2", str(src)], ["pine", "pine-compile", "demo"], ["pine", "artifacts", "demo"], ["pine", "inspect", "demo"], ["pine", "versions", "demo"], ["pine", "rollback", "demo"], ["pine", "rollback", "demo", "--to-version", "art2"], ["pine", "activate", "demo", "art1"], ["pine", "remove", "demo"]):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)
    assert ("pine1", "art3") in active and ("pine1", "art2") in active and removed == ["demo"]
    assert not artifact_dir.exists()
    assert Path.cwd().exists()
    assert runner.invoke(cli_main.cli, ["pine", "show", "missing"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["pine", "activate", "demo", "bad"]).exit_code == 0


def test_strategy_cli_lifecycle_with_fakes(monkeypatch):
    import openpine.pine.registry as pine_registry_mod
    import openpine.registry as registry_mod
    strategy_obj = SimpleNamespace(strategy_id="sid1", name="strat", pine_id="pine1", artifact_id="art1", params_hash="hash", params_json='{"a":"1"}', symbol="BTCUSDT", timeframe="15m", exchange="binance", market_type="spot", mode="paper", enabled=True, status="paused", created_at=1, updated_at=2)
    class Conn:
        def __init__(self): self.queries = []
        def execute(self, sql, params=()): self.queries.append((sql, tuple(params))); return self
        def commit(self): return None
    class FakeStrategyRegistry:
        def __init__(self): self._conn = Conn(); self._mem = {"sid1": strategy_obj}
        def list_strategies(self): return [strategy_obj]
        def get_strategy(self, strategy_id):
            if strategy_id == "missing": raise KeyError(strategy_id)
            return strategy_obj
        def register_strategy(self, **kwargs): return strategy_obj
        def update_status(self, strategy_id, status): strategy_obj.status = status
        def close(self): pass
    class FakePineRegistry:
        def get_source(self, name):
            if name == "missing": raise KeyError(name)
            return SimpleNamespace(id="pine1", active_artifact_id="art1")
        def close(self): pass
    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", FakeStrategyRegistry)
    monkeypatch.setattr(pine_registry_mod, "SQLitePineSourceRegistry", FakePineRegistry)
    runner = CliRunner()
    commands = [["strategy", "list"], ["strategy", "list", "--json"], ["strategy", "show", "sid1"], ["strategy", "status", "sid1"], ["strategy", "create", "sid1", "--pine", "demo", "--symbol", "BTCUSDT", "--timeframe", "15m", "--param", "x=1"], ["strategy", "update", "sid1", "--param", "b=2"], ["strategy", "pause", "sid1"], ["strategy", "resume", "sid1"], ["strategy", "remove", "sid1"]]
    for args in commands:
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)
    assert runner.invoke(cli_main.cli, ["strategy", "show", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "create", "sid1", "--pine", "demo", "--symbol", "BTCUSDT", "--timeframe", "15m", "--param", "bad"]).exit_code != 0
    strategy_obj.status = "error"
    assert runner.invoke(cli_main.cli, ["strategy", "resume", "sid1"]).exit_code != 0


def test_strategy_result_cli_commands_with_fake_store(monkeypatch, tmp_path: Path):
    import openpine.registry as registry_mod
    import openpine.storage as storage_mod
    import pandas as pd
    from openpine.storage.backtest_dto import (
        ARTIFACT_TYPE_EQUITY_CURVE,
        ARTIFACT_TYPE_PLOT_OUTPUTS,
        BacktestArtifact,
        BacktestMetricsSummary,
        BacktestRun,
        BacktestTrade,
    )

    strategy_obj = SimpleNamespace(
        strategy_id="sid1",
        name="strat",
        pine_id="pine1",
        artifact_id="art1",
        params_hash="hash",
        params_json="{}",
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
        mode="paper",
        enabled=True,
        status="paused",
        created_at=1,
        updated_at=2,
    )
    metrics = BacktestMetricsSummary(
        initial_capital=1000,
        final_equity=1100,
        net_profit=100,
        net_profit_pct=10,
        profit_factor=2.0,
        max_drawdown_pct=1.0,
        win_rate=50,
        trades_total=1,
        avg_win=100,
        avg_loss=-10,
        commission_total=1,
        expectancy=50,
    )
    run = BacktestRun(
        run_id="run1",
        strategy_id="sid1",
        pine_id="pine1",
        artifact_id="art1",
        params_hash="hash",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="15m",
        from_time=1,
        to_time=2,
        warmup_bars=0,
        status="done",
        started_at=1,
        finished_at=2,
        metrics=metrics,
    )
    trade = BacktestTrade(
        trade_id="t1",
        run_id="run1",
        strategy_id="sid1",
        direction="long",
        entry_time=1,
        entry_price=100.0,
        qty=1.0,
        exit_price=110.0,
        net_pnl=10.0,
        bars_held=2,
        exit_reason="tp",
    )
    eq_path = tmp_path / "equity.parquet"
    plot_path = tmp_path / "plots.parquet"
    artifacts = [
        BacktestArtifact("a1", "run1", "sid1", ARTIFACT_TYPE_EQUITY_CURVE, str(eq_path), "parquet", row_count=2),
        BacktestArtifact("a2", "run1", "sid1", ARTIFACT_TYPE_PLOT_OUTPUTS, str(plot_path), "parquet", row_count=3),
    ]

    class FakeRegistry:
        def get_strategy(self, strategy_id):
            if strategy_id == "missing":
                raise KeyError(strategy_id)
            return strategy_obj
        def update_status(self, strategy_id, status):
            strategy_obj.status = status
        def close(self):
            pass

    class FakeStore:
        def get_latest_run(self, strategy_id): return run
        def get_run(self, run_id): return run if run_id != "missing" else None
        def list_runs(self, strategy_id, limit=20): return [run]
        def list_trades(self, run_id): return [trade]
        def list_artifacts(self, run_id): return list(artifacts)
        def close(self): pass

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", FakeRegistry)
    monkeypatch.setattr(storage_mod, "BacktestResultStore", FakeStore)
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.DataFrame({"time": [1, 2], "equity": [1000, 1100], "title": ["p", "p"], "value": [1.0, 2.0]}))
    monkeypatch.setattr(cli_main, "_copy_strategy_export_run_meta", lambda **kwargs: None)
    monkeypatch.setattr(cli_main, "_compare_strategy_run_with_tv_exports", lambda **kwargs: {"comparisons": [{"type": "plots", "status": "match", "classification": "exact", "mismatch_cells": 0, "total_cells": 1, "max_abs_delta": 0.0}]})

    runner = CliRunner()
    commands = [
        ["strategy", "metrics", "sid1"],
        ["strategy", "metrics", "sid1", "--json"],
        ["strategy", "runs", "sid1"],
        ["strategy", "runs", "sid1", "--json"],
        ["strategy", "run", "run1"],
        ["strategy", "run", "run1", "--json"],
        ["strategy", "trades", "sid1"],
        ["strategy", "trades", "sid1", "--json"],
        ["strategy", "equity", "sid1"],
        ["strategy", "plots", "sid1"],
        ["strategy", "export-run", "sid1", "--output", str(tmp_path / "export"), "--no-plots", "--no-trades"],
        ["strategy", "paper", "sid1", "start"],
        ["strategy", "paper", "sid1", "stop"],
    ]
    (tmp_path / "tv.csv").write_text("time,open,high,low,close\n1,1,1,1,1\n", encoding="utf-8")
    for args in commands:
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)
    assert runner.invoke(cli_main.cli, ["strategy", "run", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "compare-tv", "sid1", "--output", str(tmp_path / "cmp2")]).exit_code != 0
    strategy_obj.status = "error"
    assert runner.invoke(cli_main.cli, ["strategy", "paper", "sid1", "start"]).exit_code != 0


def test_strategy_live_error_and_gateway_cli_edges(monkeypatch):
    import openpine.config as config_mod
    import openpine.registry as registry_mod

    strategy_obj = SimpleNamespace(strategy_id="sid1", name="strat", status="paused")
    statuses: list[str] = []

    class FakeRegistry:
        def get_strategy(self, strategy_id):
            if strategy_id == "missing":
                raise KeyError(strategy_id)
            return strategy_obj
        def update_status(self, strategy_id, status):
            statuses.append(status)
            strategy_obj.status = status
        def close(self): pass

    class FakeConfig:
        live_enabled = False
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=False))
        @classmethod
        def load(cls): return cls()

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", FakeRegistry)
    monkeypatch.setattr(config_mod, "OpenPineConfig", FakeConfig)
    runner = CliRunner()
    assert runner.invoke(cli_main.cli, ["strategy", "live", "sid1", "enable"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "live", "sid1", "stop"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "live", "sid1", "start"]).exit_code != 0
    strategy_obj.status = "error"
    assert runner.invoke(cli_main.cli, ["strategy", "live", "sid1", "enable"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "error", "sid1", "clear", "--to", "disabled"]).exit_code == 0
    assert "disabled" in statuses
    assert runner.invoke(cli_main.cli, ["strategy", "error", "sid1", "clear"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "error", "missing", "clear"]).exit_code != 0

    called = {}
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", SimpleNamespace(run=lambda *a, **k: called.update({"args": a, "kwargs": k})))
    assert runner.invoke(cli_main.cli, ["gateway", "run", "--host", "127.0.0.1", "--port", "9999", "--workers", "2"]).exit_code == 0
    assert called["kwargs"]["port"] == 9999


def test_daemon_cli_no_services(monkeypatch):
    import openpine.config as config_mod
    import openpine.daemon.refresh_service as refresh_mod

    class FakeConfig:
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=False))
        @classmethod
        def load(cls): return cls()

    class BadRefresh:
        def __init__(self): raise RuntimeError("disabled")

    monkeypatch.setattr(config_mod, "OpenPineConfig", FakeConfig)
    monkeypatch.setattr(refresh_mod, "MarketDataRefreshService", BadRefresh)
    result = CliRunner().invoke(cli_main.cli, ["daemon", "run", "--no-telegram"])
    assert result.exit_code == 0
    assert "No services" in result.output
