from __future__ import annotations

import asyncio
import importlib
import runpy
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner


cli_main = importlib.import_module("openpine.cli.main")
cli_compare = importlib.import_module("openpine.cli.compare")
cli_data = importlib.import_module("openpine.cli.data")
cli_ops = importlib.import_module("openpine.cli.ops")
cli_optimizer = importlib.import_module("openpine.cli.optimizer")
cli_reports = importlib.import_module("openpine.cli.reports")
runtime_helpers = importlib.import_module("openpine.cli.runtime_helpers")


class _SinkConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *parts, **_kwargs) -> None:
        self.lines.append(" ".join(str(part) for part in parts))


def _cfg(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    saves: list[bool] = []
    telegram = SimpleNamespace(
        enabled=False,
        chat_allowlist=[],
        token_ref="env:OPENPINE_TELEGRAM_TOKEN",
        resolve_token=lambda: "token",
    )
    cfg = SimpleNamespace(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
        duckdb_path=tmp_path / "openpine.duckdb",
        kill_switch=False,
        live_enabled=False,
        state=None,
        plugins=SimpleNamespace(telegram=telegram),
        save=lambda: saves.append(True),
        saves=saves,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _patch_config(monkeypatch, cfg: SimpleNamespace) -> None:
    import openpine.config as config_mod

    monkeypatch.setattr(config_mod.OpenPineConfig, "load", classmethod(lambda cls: cfg))


def _csv(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_run_command_strategy_compare_without_compare_from_or_tv_chart(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["strategy", "create"]:
            return "noise\nStrategy created: strat-pass52\n"
        return ""

    monkeypatch.setattr(cli_main, "_run_openpine_cli", fake_run)
    source = tmp_path / "strategy_source.pine"
    source.write_text("//@version=6\nstrategy('branch arcs')\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli_main.cli,
        [
            "run",
            str(source),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1m",
            "--from",
            "2026-01-01",
            "--compare-to",
            "2026-01-03",
            "--capture-plots",
            "--tv-trades",
            str(tmp_path / "tv_trades.csv"),
            "--output",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    backtest = next(call for call in calls if call[:2] == ["strategy", "backtest"])
    compare = next(call for call in calls if call[:2] == ["strategy", "compare-tv"])
    assert "--capture-plots" in backtest
    assert "--capture-from" not in backtest
    assert backtest[backtest.index("--capture-to") + 1] == "2026-01-03"
    assert "--tv-chart" not in compare
    assert "--tv-trades" in compare
    assert "--compare-from" not in compare
    assert compare[compare.index("--compare-to") + 1] == "2026-01-03"


def test_validate_schema_reports_only_the_side_that_is_missing(monkeypatch):
    import openpine.contracts as contracts
    import openpine.events as events

    expected_fields = {
        "strategy_id",
        "artifact_id",
        "params_hash",
        "instrument_key",
        "timeframe",
        "bar_time",
        "error_type",
        "message",
        "traceback_id",
        "job_id",
        "strategy_status_after",
    }

    monkeypatch.setattr(
        contracts,
        "StrategyRuntimeError",
        SimpleNamespace(model_fields=set(expected_fields)),
    )
    monkeypatch.setattr(
        events,
        "StrategyRuntimeErrorPayload",
        SimpleNamespace(__dataclass_fields__={}),
    )
    assert cli_main._validate_event_schema("StrategyRuntimeError") is False

    monkeypatch.setattr(
        contracts,
        "StrategyRuntimeError",
        SimpleNamespace(model_fields={}),
    )
    monkeypatch.setattr(
        events,
        "StrategyRuntimeErrorPayload",
        SimpleNamespace(__dataclass_fields__=set(expected_fields)),
    )
    assert cli_main._validate_event_schema("strategy_runtime_error") is False


def test_pine_remove_skips_missing_artifact_dir_before_next_artifact(monkeypatch, tmp_path: Path):
    import openpine.artifacts as artifacts_mod
    import openpine.pine.registry as pine_registry_mod

    existing_dir = tmp_path / "artifact-present"
    existing_dir.mkdir()
    removed: list[Path] = []

    class Registry:
        def __init__(self) -> None:
            self.closed = False

        def get_source(self, name: str):
            assert name == "demo"
            return SimpleNamespace(id="source-1")

        def remove_source(self, name: str) -> None:
            assert name == "demo"

        def close(self) -> None:
            self.closed = True

    class Store:
        def list_artifacts(self, source_id: str):
            assert source_id == "source-1"
            return [
                {"artifact_dir": str(tmp_path / "does-not-exist")},
                {"artifact_dir": str(existing_dir)},
            ]

    monkeypatch.setattr(pine_registry_mod, "SQLitePineSourceRegistry", Registry)
    monkeypatch.setattr(artifacts_mod, "ArtifactStore", Store)
    monkeypatch.setattr(cli_main.shutil, "rmtree", lambda path, ignore_errors=True: removed.append(Path(path)))

    cli_main.pine_remove.callback("demo")

    assert removed == [existing_dir]


def test_state_risk_plugins_telegram_poll_and_impossible_strategy_error_paths(monkeypatch, tmp_path: Path):
    cfg = _cfg(tmp_path, kill_switch=True)
    _patch_config(monkeypatch, cfg)

    import openpine.state.store as state_store_mod

    class Store:
        def __init__(self, root: Path) -> None:
            self.root = root

        def list_snapshots(self, strategy_id: str):  # pragma: no cover - should not be reached here
            raise AssertionError(strategy_id)

    monkeypatch.setattr(state_store_mod, "StateStore", Store)
    cli_main.state_list.callback(None)

    cli_main.risk_kill_switch.callback("off")
    assert cfg.kill_switch is False

    cli_main.plugins_enable.callback("telegram", None)
    assert cfg.plugins.telegram.enabled is True

    monkeypatch.setattr(
        cli_main,
        "_load_telegram_command_catalog",
        lambda: [
            {"command": "/plain", "title": "Plain"},
            {"command": "/withcli", "title": "With CLI", "cli": "openpine version"},
        ],
    )
    monkeypatch.setattr(
        cli_main,
        "_telegram_menu_markup",
        lambda: {"inline_keyboard": [[{"text": "Status"}]]},
    )
    cli_main.plugins_telegram_commands.callback("text")

    cli_main.plugins_telegram_poll.callback(
        once=False,
        limit=1,
        offset=None,
        timeout=0,
        dry_run=True,
        fake_updates_json='{"result": []}',
    )

    # Click restricts this argument to "clear", but the callback still has a
    # defensive fall-through branch; call it directly to cover that arc safely.
    cli_main.strategy_error.callback("strat", "noop", "paused")


def test_cli_main_module_guard_executes_help(monkeypatch):
    main_file = Path(str(cli_main.__file__))
    monkeypatch.setattr(sys, "argv", [str(main_file), "--help"])

    try:
        runpy.run_path(str(main_file), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover - click --help should exit
        raise AssertionError("main guard did not raise SystemExit")


def test_daemon_run_remaining_platform_and_service_selection_branches(monkeypatch, tmp_path: Path):
    import openpine.daemon.refresh_service as refresh_mod

    cfg = _cfg(tmp_path)
    cfg.plugins.telegram.enabled = False
    _patch_config(monkeypatch, cfg)
    runner = CliRunner()

    class FailingRefresh:
        def __init__(self) -> None:
            raise RuntimeError("refresh disabled")

    monkeypatch.setattr(refresh_mod, "MarketDataRefreshService", FailingRefresh)
    no_services = runner.invoke(cli_main.cli, ["daemon", "run"])
    assert no_services.exit_code == 0, no_services.output
    assert "No services configured" in no_services.output

    class RefreshService:
        name = "refresh"

        async def start(self) -> None:
            return None

        async def stop(self, timeout: float = 5.0) -> None:
            return None

    async def cancelled_sleep(_delay: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(refresh_mod, "MarketDataRefreshService", RefreshService)
    monkeypatch.setattr(asyncio, "sleep", cancelled_sleep)
    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    win32_run = runner.invoke(cli_main.cli, ["daemon", "run"])
    assert win32_run.exit_code == 0, win32_run.output
    assert "Daemon stopped" in win32_run.output

    class FakeLoop:
        def __init__(self) -> None:
            self.stopped = False
            self.removed: list[signal.Signals] = []

        def add_signal_handler(self, sig, callback, *args) -> None:
            monkeypatch.setattr(cli_main.sys, "platform", "win32")
            callback(*args)

        def remove_signal_handler(self, sig) -> None:
            self.removed.append(sig)

        def stop(self) -> None:
            self.stopped = True

    fake_loop = FakeLoop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fake_loop)
    monkeypatch.setattr(cli_main.sys, "platform", "linux")
    signal_run = runner.invoke(cli_main.cli, ["daemon", "run"])
    assert signal_run.exit_code == 0, signal_run.output
    assert fake_loop.stopped is True
    assert fake_loop.removed == []


def test_compare_row_helpers_cover_second_mismatch_and_match_paths(tmp_path: Path):
    tv_time = _csv(
        tmp_path / "tv_time.csv",
        "time,plot\n1000,10\n2000,20\n3000,30\n",
    )
    op_time = _csv(
        tmp_path / "op_time.csv",
        "bar_time,plot\n1000,15\n2000,21\n3000,30\n",
    )
    summary, top = cli_compare._compare_rows_by_time(
        tv_path=tv_time,
        op_path=op_time,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert summary["status"] == "mismatch"
    assert top[0]["first_bad"]["abs_delta"] == 5.0

    tv_time_match = _csv(tmp_path / "tv_time_match.csv", "time,plot\n1000,1\n")
    op_time_match = _csv(tmp_path / "op_time_match.csv", "bar_time,plot\n1000,1\n")
    match_summary, _ = cli_compare._compare_rows_by_time(
        tv_path=tv_time_match,
        op_path=op_time_match,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert match_summary["classification"] == "match"

    tv_order = _csv(tmp_path / "tv_order.csv", "a,b\n10,foo\n20,bar\n30,baz\n")
    op_order = _csv(tmp_path / "op_order.csv", "a,b\n15,foo\n21,bar\n30,baz\n")
    order_summary, order_top = cli_compare._compare_rows_by_order(
        tv_path=tv_order,
        op_path=op_order,
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert order_summary["status"] == "mismatch"
    assert order_top[0]["first_bad"]["abs_delta"] == 5.0

    tv_order_match = _csv(tmp_path / "tv_order_match.csv", "a,b\n1,same\n")
    op_order_match = _csv(tmp_path / "op_order_match.csv", "a,b\n1,same\n")
    order_match_summary, _ = cli_compare._compare_rows_by_order(
        tv_path=tv_order_match,
        op_path=op_order_match,
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert order_match_summary["classification"] == "match"


def test_compare_strategy_export_match_results_skip_all_failure_lists(monkeypatch, tmp_path: Path):
    def match_summary(kind: str) -> dict[str, object]:
        return {
            "status": "match",
            "classification": "match",
            "tv_file": f"tv-{kind}.csv",
            "openpine_file": f"op-{kind}.csv",
            "tv_rows": 1,
            "openpine_rows": 1,
            "common_times": 1,
            "common_columns": 1,
            "total_cells": 1,
            "mismatch_cells": 0,
            "mismatch_ratio": 0.0,
            "max_abs_delta": 0.0,
            "worst_column": None,
            "worst_time_ms": None,
        }

    time_calls: list[Path] = []

    def fake_compare_rows_by_time(**kwargs):
        time_calls.append(Path(kwargs["op_path"]))
        kind = "equity" if Path(kwargs["op_path"]).name.startswith("equity") else "plots"
        return match_summary(kind), []

    def fake_compare_rows_by_order(**_kwargs):
        return match_summary("trades"), []

    def fake_normalize(**kwargs):
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("trade_id\n", encoding="utf-8")
        return output_path

    monkeypatch.setattr(cli_compare, "_compare_rows_by_time", fake_compare_rows_by_time)
    monkeypatch.setattr(cli_compare, "_compare_rows_by_order", fake_compare_rows_by_order)
    monkeypatch.setattr(cli_compare, "_write_normalized_tv_trades", fake_normalize)

    result = cli_compare._compare_strategy_run_with_tv_exports(
        strategy_id="s1",
        run=SimpleNamespace(run_id="run-52"),
        exported={
            "plots": str(tmp_path / "plots.csv"),
            "equity": str(tmp_path / "equity.csv"),
            "trades": str(tmp_path / "trades.csv"),
        },
        output_path=tmp_path / "compare",
        tv_chart=str(tmp_path / "tv_chart.csv"),
        tv_trades=str(tmp_path / "tv_trades.csv"),
        tv_equity=str(tmp_path / "tv_equity.csv"),
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
        compare_from_ms=None,
        compare_to_ms=None,
    )

    assert [row["type"] for row in result["comparisons"]] == ["plots", "equity", "trades"]
    assert result["failures"] == []
    assert (tmp_path / "compare" / "comparison_summary.json").exists()


def test_data_status_sync_backfill_and_repair_leftover_branches(monkeypatch, tmp_path: Path):
    import openpine.data.orchestrator as orchestrator_mod
    import openpine.data.provider_adapter as provider_adapter_mod
    import openpine.registry as registry_mod
    import openpine.storage as storage_mod

    class FakeOrchestrator:
        def __init__(self, provider=None) -> None:
            self._provider = provider

        def load_bars(self, _query):
            return SimpleNamespace(bars=[object()])

    monkeypatch.setattr(orchestrator_mod, "DataOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(provider_adapter_mod, "create_local_marketdata_provider_adapter", lambda: object())
    monkeypatch.setattr(signal, "signal", lambda *_args: None)
    timer_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(signal, "setitimer", lambda *args: timer_calls.append(args))

    assert cli_data._run_sync_marketdata_backfill(
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market="spot",
        start_ms=0,
        end_ms=60_000,
        timeout=1,
        console=_SinkConsole(),
    ) is True
    assert timer_calls[-1] == (signal.ITIMER_REAL, 0)

    cfg = _cfg(tmp_path)
    candles = cfg.data_dir / "candles"
    candles.mkdir(parents=True)
    (candles / "part.parquet").write_text("not really parquet", encoding="utf-8")
    _patch_config(monkeypatch, cfg)

    class Storage:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def execute(self, _sql):
            return SimpleNamespace(fetchall=lambda: [])

        def close(self) -> None:
            pass

    monkeypatch.setattr(storage_mod, "SQLiteStorage", Storage)
    status = CliRunner().invoke(cli_data.data, ["status"])
    assert status.exit_code == 0, status.output
    assert "Parquet candle files: 1" in status.output

    enqueued: list[object] = []
    monkeypatch.setattr(
        cli_data,
        "_cli_scheduler",
        SimpleNamespace(enqueue=lambda job: enqueued.append(job) or job),
    )

    class Registry:
        def __init__(self) -> None:
            self.updated: list[tuple[str, str]] = []

        def list_strategies(self):
            return [
                SimpleNamespace(
                    strategy_id="paused-match",
                    symbol="btcusdt",
                    timeframe="1m",
                    exchange="BINANCE",
                    status="paused",
                ),
                SimpleNamespace(
                    strategy_id="disabled-match",
                    symbol="BTCUSDT",
                    timeframe="1m",
                    exchange="binance",
                    status="disabled",
                ),
            ]

        def update_status(self, strategy_id: str, status: str) -> None:
            self.updated.append((strategy_id, status))

        def close(self) -> None:
            pass

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", Registry)
    repair = CliRunner().invoke(
        cli_data.data,
        ["repair", "BTCUSDT", "1m", "--from", "1000", "--to", "2000"],
    )
    assert repair.exit_code == 0, repair.output
    assert len(enqueued) == 1
    assert "paused-match" in repair.output
    assert "disabled-match" in repair.output


def test_ops_optimizer_reports_and_runtime_easy_leftovers(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    job = SimpleNamespace(
        id="job-pass52",
        job_type=SimpleNamespace(value="backfill"),
        status=SimpleNamespace(value="done"),
        strategy_id=None,
        priority=3,
        idempotency_key=None,
        created_at=0,
        started_at=None,
        finished_at=None,
        error=None,
        result={"ok": True},
    )
    monkeypatch.setattr(cli_ops, "_cli_scheduler", SimpleNamespace(get_job=lambda _job_id: job))
    shown = runner.invoke(cli_ops.jobs, ["show", "job-pass52"])
    assert shown.exit_code == 0, shown.output
    assert "result" in shown.output

    monkeypatch.setattr(cli_ops, "_systemd_available", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="", stderr="status stderr", returncode=0),
    )
    status = runner.invoke(cli_ops.service, ["status"])
    assert status.exit_code == 0, status.output
    assert "status stderr" in status.output

    class OptimizerService:
        def validate_config(self, *, strategy_id: str, trials: int):
            return SimpleNamespace(
                strategy_id=strategy_id,
                trials_requested=trials,
                status="ok",
                reason=None,
            )

    import openpine.optimizer as optimizer_mod

    monkeypatch.setattr(optimizer_mod, "OptimizerService", OptimizerService)
    optimized = runner.invoke(
        cli_optimizer.optimizer,
        ["dry-run", "--strategy", "s-pass52", "--trials", "2"],
    )
    assert optimized.exit_code == 0, optimized.output
    assert "reason:" not in optimized.output

    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)
    listed = runner.invoke(cli_reports.reports, ["list"])
    assert listed.exit_code == 0, listed.output
    assert "Available Reports" in listed.output
    assert "Report files" not in listed.output

    import openpine.state.store as state_store_mod

    class Store:
        def __init__(self, root: Path) -> None:
            self.root = root

        def save_runtime_snapshot(self, **_kwargs):
            return None

    monkeypatch.setattr(state_store_mod, "StateStore", Store)
    console = _SinkConsole()
    runtime_helpers._save_strategy_resume_snapshot(
        strategy=SimpleNamespace(
            strategy_id="s-pass52",
            artifact_id="artifact",
            params_hash="hash",
            exchange="BINANCE",
            market_type="SPOT",
            symbol="btcusdt",
            timeframe="1m",
        ),
        prepared=SimpleNamespace(
            bars=[
                SimpleNamespace(
                    time=60_000,
                    time_close=120_000,
                    open=1.0,
                    high=2.0,
                    low=0.5,
                    close=1.5,
                    volume=10.0,
                )
            ],
            end_ms=120_000,
        ),
        result=SimpleNamespace(resume_state={"runtime_state": {}}),
        console=console,
    )
    assert not any("State snapshot saved" in line for line in console.lines)
