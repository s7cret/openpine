from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")


class _Conn:
    def __init__(self) -> None:
        self.statements: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def execute(self, *args, **kwargs):
        self.statements.append((args, kwargs))
        return self

    def commit(self) -> None:
        pass


class _FakeRegistry:
    strategy = SimpleNamespace(
        strategy_id="s1",
        id="s1",
        name="Demo Strategy",
        pine_id="pine1",
        artifact_id="art1",
        active_artifact_id="art1",
        params_hash="hash1",
        params_json='{"fast": 2}',
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        status="paused",
        enabled=False,
    )
    missing = False
    statuses: list[tuple[str, str]] = []

    def __init__(self) -> None:
        self._conn = _Conn()
        self._mem = {"s1": type(self).strategy}

    def get_strategy(self, strategy_id: str):
        if type(self).missing or strategy_id == "missing":
            raise KeyError(strategy_id)
        return type(self).strategy

    def list_strategies(self, status: str | None = None):
        return [type(self).strategy]

    def update_status(self, strategy_id: str, status: str) -> None:
        type(self).statuses.append((strategy_id, status))
        type(self).strategy.status = status

    def close(self) -> None:
        pass


class _FakeStore:
    run = None
    runs: list[object] = []
    trades: list[object] = []
    artifacts: list[object] = []

    def get_run(self, run_id: str):
        return type(self).run if run_id == "run1" else None

    def get_latest_run(self, strategy_id: str):
        return type(self).run

    def list_runs(self, strategy_id: str, limit: int = 20):
        return list(type(self).runs)[:limit]

    def list_trades(self, run_id: str):
        return list(type(self).trades)

    def list_artifacts(self, run_id: str):
        return list(type(self).artifacts)

    def close(self) -> None:
        pass


def _metrics(**overrides):
    values = dict(
        initial_capital=10_000.0,
        final_equity=10_500.0,
        net_profit=500.0,
        net_profit_pct=5.0,
        gross_profit=700.0,
        gross_loss=-200.0,
        profit_factor=3.5,
        max_drawdown=100.0,
        max_drawdown_pct=1.0,
        sharpe=1.2,
        sortino=1.5,
        win_rate=0.6,
        trades_total=12,
        winning_trades=7,
        losing_trades=5,
        avg_trade=41.6,
        avg_win=100.0,
        avg_loss=-40.0,
        commission_total=3.0,
        expectancy=41.6,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_registry_and_store(monkeypatch, tmp_path: Path):
    import openpine.registry as registry_mod
    import openpine.storage as storage_mod
    import openpine.config as config_mod

    _FakeRegistry.missing = False
    _FakeRegistry.statuses = []
    _FakeRegistry.strategy = SimpleNamespace(**_FakeRegistry.strategy.__dict__)
    _FakeRegistry.strategy.status = "paused"
    _FakeRegistry.strategy.enabled = False

    run = SimpleNamespace(
        run_id="run1",
        strategy_id="s1",
        pine_id="pine1",
        artifact_id="art1",
        params_hash="hash1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1m",
        from_time=1,
        to_time=3,
        warmup_bars=0,
        status="ok",
        started_at=1,
        finished_at=2,
        metrics=_metrics(),
    )
    trades = [
        SimpleNamespace(
            trade_id=f"t{i}",
            run_id="run1",
            strategy_id="s1",
            direction="long" if i % 2 else "short",
            entry_time=i,
            exit_time=i + 1,
            entry_price=100.0 + i,
            exit_price=101.0 + i,
            qty=0.1,
            gross_pnl=1.0,
            fee=0.01,
            net_pnl=1.0 if i % 2 else -0.5,
            bars_held=i,
            exit_reason="tp",
        )
        for i in range(12)
    ]
    _FakeStore.run = run
    _FakeStore.runs = [run]
    _FakeStore.trades = trades

    plot_path = tmp_path / "plots.parquet"
    pd.DataFrame(
        [
            {"bar_time": 1, "bar_index": 0, "title": "p", "value": 10.0},
            {"bar_time": 2, "bar_index": 1, "title": "p", "value": 11.0},
        ]
    ).to_parquet(plot_path)
    plain_plot_path = tmp_path / "plots_no_title.parquet"
    pd.DataFrame([{"bar_time": 1, "bar_index": 0, "value": 10.0}]).to_parquet(
        plain_plot_path
    )
    equity_path = tmp_path / "equity.parquet"
    pd.DataFrame([{"time": 1, "equity": 100.0}, {"time": 2, "equity": 101.0}]).to_parquet(
        equity_path
    )

    plot_artifact = SimpleNamespace(
        artifact_row_id="a1",
        run_id="run1",
        strategy_id="s1",
        artifact_type="plot_outputs",
        path=str(plot_path),
        format="parquet",
        row_count=2,
    )
    plain_plot_artifact = SimpleNamespace(**{**plot_artifact.__dict__, "path": str(plain_plot_path)})
    equity_artifact = SimpleNamespace(
        artifact_row_id="a2",
        run_id="run1",
        strategy_id="s1",
        artifact_type="equity_curve",
        path=str(equity_path),
        format="parquet",
        row_count=2,
    )
    _FakeStore.artifacts = [plot_artifact, equity_artifact]

    data_dir = tmp_path / "data"
    meta_dir = data_dir / "backtests" / "s1" / "run1"
    meta_dir.mkdir(parents=True)
    (meta_dir / "run_meta.json").write_text('{"run_id":"run1"}', encoding="utf-8")
    cfg = SimpleNamespace(data_dir=data_dir, live_enabled=True)

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", _FakeRegistry)
    monkeypatch.setattr(storage_mod, "BacktestResultStore", _FakeStore)
    monkeypatch.setattr(config_mod.OpenPineConfig, "load", classmethod(lambda cls: cfg))
    return SimpleNamespace(
        run=run,
        trades=trades,
        plot_artifact=plot_artifact,
        plain_plot_artifact=plain_plot_artifact,
        equity_artifact=equity_artifact,
        cfg=cfg,
    )


def test_strategy_history_commands_cover_json_text_empty_and_missing(monkeypatch, tmp_path):
    fixtures = _install_registry_and_store(monkeypatch, tmp_path)
    runner = CliRunner()

    for args in (
        ["strategy", "enable", "s1"],
        ["strategy", "disable", "s1"],
        ["strategy", "pause", "s1"],
        ["strategy", "resume", "s1"],
        ["strategy", "remove", "s1"],
    ):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)

    for args in (
        ["strategy", "metrics", "s1"],
        ["strategy", "metrics", "s1", "--run-id", "run1", "--json"],
        ["strategy", "runs", "s1"],
        ["strategy", "runs", "s1", "--json"],
        ["strategy", "run", "run1"],
        ["strategy", "run", "run1", "--json"],
        ["strategy", "trades", "s1"],
        ["strategy", "trades", "s1", "--run-id", "run1", "--json"],
        ["strategy", "equity", "s1", "--run-id", "run1", "--tail", "1"],
    ):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)

    _FakeStore.artifacts = [fixtures.plain_plot_artifact]
    result = runner.invoke(cli_main.cli, ["strategy", "plots", "s1", "--run-id", "run1"])
    assert result.exit_code == 0, result.output
    assert "Columns" in result.output

    _FakeStore.run = None
    _FakeStore.runs = []
    for args in (
        ["strategy", "metrics", "s1"],
        ["strategy", "runs", "s1"],
        ["strategy", "trades", "s1"],
        ["strategy", "equity", "s1"],
        ["strategy", "plots", "s1"],
        ["strategy", "export-run", "s1", "--output", str(tmp_path / "empty-export")],
    ):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code != 0, (args, result.output)

    _FakeStore.run = fixtures.run
    _FakeRegistry.missing = True
    for args in (
        ["strategy", "enable", "missing"],
        ["strategy", "disable", "missing"],
        ["strategy", "metrics", "missing"],
        ["strategy", "runs", "missing"],
        ["strategy", "trades", "missing"],
        ["strategy", "equity", "missing"],
        ["strategy", "plots", "missing"],
        ["strategy", "export-run", "missing", "--output", str(tmp_path / "missing-export")],
    ):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code != 0, (args, result.output)


def test_strategy_export_compare_paper_live_and_daemon_gateway_paths(monkeypatch, tmp_path):
    fixtures = _install_registry_and_store(monkeypatch, tmp_path)
    runner = CliRunner()

    out = tmp_path / "export"
    result = runner.invoke(
        cli_main.cli,
        [
            "strategy",
            "export-run",
            "s1",
            "--run-id",
            "run1",
            "--output",
            str(out),
            "--compare-from",
            "1",
            "--compare-to",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "plots.csv").exists()
    assert (out / "trades.csv").exists()
    assert (out / "metrics.json").exists()
    assert (out / "run_meta.json").exists()

    result = runner.invoke(
        cli_main.cli,
        ["strategy", "compare-tv", "s1", "--output", str(tmp_path / "cmp")],
    )
    assert result.exit_code != 0

    monkeypatch.setattr(
        cli_main,
        "_compare_strategy_run_with_tv_exports",
        lambda **kwargs: {
            "comparisons": [
                {
                    "type": "plots",
                    "status": "ok",
                    "classification": "match",
                    "mismatch_cells": 0,
                    "total_cells": 2,
                    "max_abs_delta": 0.0,
                }
            ]
        },
    )
    tv_chart = tmp_path / "tv.csv"
    tv_chart.write_text("bar_time,p\n1,10\n", encoding="utf-8")
    result = runner.invoke(
        cli_main.cli,
        [
            "strategy",
            "compare-tv",
            "s1",
            "--run-id",
            "run1",
            "--tv-chart",
            str(tv_chart),
            "--output",
            str(tmp_path / "cmp2"),
            "--compare-from",
            "1",
            "--compare-to",
            "3",
            "--include-base-columns",
        ],
    )
    assert result.exit_code == 0, result.output

    for args in (
        ["strategy", "paper", "s1", "start"],
        ["strategy", "paper", "s1", "stop"],
        ["strategy", "live", "s1", "enable"],
        ["strategy", "live", "s1", "start"],
        ["strategy", "live", "s1", "stop"],
    ):
        fixtures.cfg.live_enabled = True
        fixtures.run.status = "ok"
        _FakeRegistry.strategy.status = "paused"
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)

    fixtures.cfg.live_enabled = False
    result = runner.invoke(cli_main.cli, ["strategy", "live", "s1", "start"])
    assert result.exit_code != 0
    fixtures.cfg.live_enabled = True
    _FakeRegistry.strategy.status = "error"
    for args in (
        ["strategy", "paper", "s1", "start"],
        ["strategy", "live", "s1", "enable"],
        ["strategy", "live", "s1", "start"],
    ):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code != 0, (args, result.output)

    _FakeRegistry.strategy.status = "paused"
    result = runner.invoke(cli_main.cli, ["strategy", "error", "s1", "clear"])
    assert result.exit_code != 0
    _FakeRegistry.strategy.status = "error"
    result = runner.invoke(cli_main.cli, ["strategy", "error", "s1", "clear", "--to", "disabled"])
    assert result.exit_code == 0, result.output

    class RefreshService:
        name = "refresh"

        async def start(self):
            pass

        async def stop(self, timeout=5.0):
            pass

    class TelegramService(RefreshService):
        name = "telegram"

    refresh_mod = types.ModuleType("openpine.daemon.refresh_service")
    refresh_mod.MarketDataRefreshService = RefreshService
    telegram_mod = types.ModuleType("openpine.daemon.telegram_service")
    telegram_mod.TelegramDaemonService = TelegramService
    monkeypatch.setitem(sys.modules, "openpine.daemon.refresh_service", refresh_mod)
    monkeypatch.setitem(sys.modules, "openpine.daemon.telegram_service", telegram_mod)
    fixtures.cfg.plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=True))

    import asyncio

    calls = {"sleep": 0}

    async def stop_sleep(_seconds):
        calls["sleep"] += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", stop_sleep)
    result = runner.invoke(cli_main.cli, ["daemon", "run"])
    assert result.exit_code == 0, result.output
    assert calls["sleep"] == 1

    uvicorn_mod = types.SimpleNamespace(run=lambda *args, **kwargs: kwargs)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_mod)
    result = runner.invoke(cli_main.cli, ["gateway", "run", "--host", "127.0.0.1", "--port", "9999", "--reload"])
    assert result.exit_code == 0, result.output

    called = []
    monkeypatch.setattr(cli_main, "cli", lambda: called.append(True) or 7)
    try:
        cli_main.main()
    except SystemExit as exc:
        assert exc.code == 7
    assert called
