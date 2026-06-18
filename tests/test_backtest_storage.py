"""Tests for backtest persistence layer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openpine.storage import (
    BacktestResultStore,
    BacktestRunRequest,
    MigrationRunner,
    SQLiteStorage,
)
from openpine.storage.backtest_dto import (
    ARTIFACT_TYPE_EQUITY_CURVE,
    ARTIFACT_TYPE_PLOT_OUTPUTS,
    ARTIFACT_TYPE_REPORT_JSON,
    ARTIFACT_TYPE_TRADES,
)


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = Path(f.name)
    storage = SQLiteStorage(path)
    runner = MigrationRunner()
    runner.run_migrations(storage)
    yield storage
    storage.close()
    path.unlink(missing_ok=True)


@pytest.fixture
def store(tmp_db):
    return BacktestResultStore(tmp_db)


class FakeTrade:
    def __init__(self, id, direction, entry_time, entry_price, qty, **kwargs):
        self.id = id
        self.direction = direction
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.qty = qty
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeEquityPoint:
    def __init__(
        self,
        time,
        equity,
        cash=0,
        position_size=0,
        position_avg_price=None,
        open_profit=0,
        realized_profit=0,
        drawdown=0,
        drawdown_percent=0,
    ):
        self.time = time
        self.equity = equity
        self.cash = cash
        self.position_size = position_size
        self.position_avg_price = position_avg_price
        self.open_profit = open_profit
        self.realized_profit = realized_profit
        self.drawdown = drawdown
        self.drawdown_percent = drawdown_percent


class FakeResult:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_migration_creates_tables(tmp_db):
    cursor = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "backtest_runs" in tables
    assert "backtest_trades" in tables
    assert "backtest_artifacts" in tables


def test_result_store_runs_migrations_for_default_storage(tmp_path, monkeypatch):
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))

    store = BacktestResultStore()
    try:
        tables = {
            row[0]
            for row in store._storage.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        store.close()

    assert "backtest_runs" in tables


def test_no_candle_data_table(tmp_db):
    cursor = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "candle_data" not in tables


def test_create_run_inserts_running(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)
    assert run_id.startswith("run_")

    run = store.get_run(run_id)
    assert run is not None
    assert run.status == "running"
    assert run.strategy_id == "strat_1"


def test_mark_cancelled_updates_run_status(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    store.mark_cancelled(run_id, "user cancelled")

    run = store.get_run(run_id)
    assert run is not None
    assert run.status == "cancelled"
    assert run.finished_at is not None


def test_save_result_updates_status_and_metrics(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    result = FakeResult(
        initial_capital=10000.0,
        final_equity=10050.0,
        net_profit=50.0,
        net_profit_percent=0.5,
        profit_factor=1.5,
        max_drawdown=10.0,
        max_drawdown_percent=0.1,
        sharpe_ratio=0.5,
        sortino_ratio=0.6,
        win_rate=50.0,
        total_trades=10,
        winning_trades=5,
        losing_trades=5,
    )
    trades = [
        FakeTrade(
            "T1",
            "long",
            1000,
            100.0,
            1.0,
            exit_time=2000,
            exit_price=110.0,
            profit=10.0,
        ),
        FakeTrade(
            "T2",
            "short",
            3000,
            110.0,
            1.0,
            exit_time=4000,
            exit_price=105.0,
            profit=5.0,
        ),
    ]
    equity = [
        FakeEquityPoint(1000, 10000.0),
        FakeEquityPoint(2000, 10010.0),
        FakeEquityPoint(3000, 10010.0),
        FakeEquityPoint(4000, 10015.0),
    ]

    store.save_result(run_id, result, trades, equity_curve=equity)

    run = store.get_run(run_id)
    assert run.status == "done"
    assert run.metrics.net_profit == 50.0
    assert run.metrics.max_drawdown_pct == 0.1
    assert run.metrics.trades_total == 10
    assert run.equity_curve_path is not None

    metrics_payload = store.get_metrics(run_id)
    assert metrics_payload is not None
    assert metrics_payload["metrics"]["max_drawdown_pct"] == 0.1

    # Older report.json artifacts did not include max_drawdown_pct. The API must
    # still serve the persisted percentage instead of letting the UI format the
    # cash drawdown as a percent (e.g. 129379 -> 129379%).
    report_path = Path(run.report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metrics"].pop("max_drawdown_pct")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    backfilled_metrics = store.get_metrics(run_id)
    assert backfilled_metrics is not None
    assert backfilled_metrics["metrics"]["max_drawdown_pct"] == 0.1


def test_metrics_from_result_preserves_summary_mapping():
    result = FakeResult(
        initial_capital=10000.0,
        final_equity=10050.0,
        net_profit=50.0,
        net_profit_percent=0.5,
        gross_profit=75.0,
        gross_loss=-25.0,
        profit_factor=1.5,
        max_drawdown=10.0,
        max_drawdown_percent=0.1,
        sharpe_ratio=0.5,
        sortino_ratio=0.6,
        win_rate=50.0,
        total_trades=10,
        winning_trades=5,
        losing_trades=5,
        avg_trade=5.0,
        avg_win=15.0,
        avg_loss=-5.0,
        largest_win=25.0,
        largest_loss=-10.0,
        avg_bars_in_trade=3.0,
        commission_total=1.25,
        expectancy=2.5,
    )

    metrics = BacktestResultStore._metrics_from_result(result)

    assert metrics.initial_capital == 10000.0
    assert metrics.net_profit_pct == 0.5
    assert metrics.max_drawdown_pct == 0.1
    assert metrics.sharpe == 0.5
    assert metrics.sortino == 0.6
    assert metrics.trades_total == 10
    assert metrics.winning_trades == 5
    assert metrics.losing_trades == 5
    assert metrics.commission_total == 1.25
    assert metrics.expectancy == 2.5
    assert metrics.calmar is None


def test_report_markdown_renders_core_summary_fields():
    result = FakeResult(symbol="BTCUSDT", timeframe="15m")
    metrics = BacktestResultStore._metrics_from_result(
        FakeResult(
            initial_capital=10000.0,
            final_equity=10050.0,
            net_profit=50.0,
            net_profit_percent=0.5,
            profit_factor=1.5,
            max_drawdown=10.0,
            sharpe_ratio=0.5,
            win_rate=50.0,
            total_trades=10,
        )
    )

    markdown = BacktestResultStore._report_markdown("run_1", "strat_1", result, metrics)

    assert "# Backtest Report: run_1" in markdown
    assert "- **Strategy**: strat_1" in markdown
    assert "- **Symbol**: BTCUSDT 15m" in markdown
    assert "| Net Profit | 50.0 |" in markdown
    assert "| Total Trades | 10 |" in markdown


def test_result_artifact_record_helpers_map_equity_and_trades():
    equity_records = BacktestResultStore._equity_curve_records(
        [FakeEquityPoint(1000, 10000.0, cash=9990.0, position_size=1)]
    )
    trade_records = BacktestResultStore._trade_artifact_records(
        [
            FakeTrade(
                "T1",
                "long",
                1000,
                100.0,
                1.0,
                exit_time=2000,
                exit_price=110.0,
                profit=10.0,
                bars_held=3,
            )
        ]
    )

    assert equity_records[0]["equity"] == 10000.0
    assert equity_records[0]["cash"] == 9990.0
    assert trade_records[0]["trade_id"] == "T1"
    assert trade_records[0]["exit_price"] == 110.0
    assert trade_records[0]["bars_held"] == 3


def test_result_database_row_helpers_map_trades_and_artifacts(tmp_path: Path):
    trade_rows = BacktestResultStore._trade_db_rows(
        run_id="run_1",
        strategy_id="strat_1",
        trades=[
            FakeTrade(
                "T1",
                "long",
                1000,
                100.0,
                1.0,
                exit_time=2000,
                exit_price=110.0,
                profit=10.0,
                profit_percent=0.1,
                commission_entry=0.25,
                commission_exit=0.25,
                bars_held=3,
                exit_reason="take_profit",
            )
        ],
        now=3000,
    )
    artifact_path = tmp_path / "report.json"
    artifact_path.write_text("{}", encoding="utf-8")
    artifact_row = BacktestResultStore._artifact_db_row(
        run_id="run_1",
        strategy_id="strat_1",
        artifact_type=ARTIFACT_TYPE_REPORT_JSON,
        path=artifact_path,
        now=3000,
    )

    assert trade_rows[0] == (
        "run_1_trade_0",
        "run_1",
        "strat_1",
        "T1",
        None,
        "long",
        1000,
        2000,
        100.0,
        110.0,
        None,
        None,
        1.0,
        10.0,
        10.0,
        0.1,
        0.5,
        0.0,
        3,
        "take_profit",
        3000,
    )
    assert artifact_row == (
        "run_1_report_json",
        "run_1",
        "strat_1",
        ARTIFACT_TYPE_REPORT_JSON,
        str(artifact_path),
        "json",
        None,
        3000,
    )


def test_plot_records_support_tuple_records_and_na_values():
    class FakeNA:
        pass

    FakeNA.__name__ = "PineNASentinel"
    records = BacktestResultStore._plot_records([(1000, 7, FakeNA(), "plot A")])

    assert records == [
        {"bar_time": 1000, "bar_index": 7, "value": None, "title": "plot A"}
    ]


def test_save_result_persists_plot_outputs_artifact(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    store.save_result(
        run_id,
        FakeResult(initial_capital=10000.0, net_profit=10.0),
        [],
        plots=[(1000, 0, 42.0, "plot A")],
    )

    artifacts = store.list_artifacts(run_id)
    types = {a.artifact_type for a in artifacts}
    run = store.get_run(run_id)
    assert ARTIFACT_TYPE_PLOT_OUTPUTS in types
    assert run is not None
    assert run.plot_outputs_path is not None
    assert Path(run.plot_outputs_path).exists()


def test_save_result_inserts_trades(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    result = FakeResult(
        initial_capital=10000.0,
        net_profit=10.0,
        total_trades=2,
    )
    trades = [
        FakeTrade("T1", "long", 1000, 100.0, 1.0, profit=10.0),
        FakeTrade("T2", "short", 2000, 110.0, 1.0, profit=-5.0),
    ]

    store.save_result(run_id, result, trades)

    saved_trades = store.list_trades(run_id)
    assert len(saved_trades) == 2
    assert saved_trades[0].direction == "long"
    assert saved_trades[1].direction == "short"


def test_save_result_creates_artifacts(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    result = FakeResult(initial_capital=10000.0, net_profit=10.0)
    trades = [FakeTrade("T1", "long", 1000, 100.0, 1.0, profit=10.0)]
    equity = [FakeEquityPoint(1000, 10000.0), FakeEquityPoint(2000, 10010.0)]

    store.save_result(run_id, result, trades, equity_curve=equity)

    artifacts = store.list_artifacts(run_id)
    types = {a.artifact_type for a in artifacts}
    assert ARTIFACT_TYPE_EQUITY_CURVE in types
    assert ARTIFACT_TYPE_TRADES in types
    assert ARTIFACT_TYPE_REPORT_JSON in types


def test_save_result_does_not_publish_corrupt_parquet_artifacts(store, monkeypatch, tmp_path):
    store._data_dir = tmp_path / "backtests"
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    def fail_count(path: Path):
        if path.name == "trades.parquet":
            raise ValueError("corrupt parquet")
        return 0

    monkeypatch.setattr("openpine.storage.backtest_storage.parquet.row_count", fail_count)

    with pytest.raises(RuntimeError, match="corrupt parquet"):
        store.save_result(
            run_id,
            FakeResult(initial_capital=10000.0, net_profit=10.0),
            [FakeTrade("T1", "long", 1000, 100.0, 1.0, profit=10.0)],
        )

    run_dir = store._run_dir("strat_1", run_id)
    assert not run_dir.exists()
    assert not run_dir.with_suffix(".tmp").exists()


def test_mark_failed(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    store.mark_failed(run_id, "Test error", traceback_id="tb_1")

    run = store.get_run(run_id)
    assert run.status == "failed"
    assert run.error_message == "Test error"
    assert run.traceback_id == "tb_1"


def test_get_latest_run(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    store.create_run(req)
    import time

    time.sleep(0.01)  # Ensure different timestamps
    run_id_2 = store.create_run(req)

    latest = store.get_latest_run("strat_1")
    assert latest.run_id == run_id_2


def test_list_runs(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    store.create_run(req)
    store.create_run(req)
    store.create_run(req)

    runs = store.list_runs("strat_1", limit=2)
    assert len(runs) == 2


def test_repeated_backtests_create_separate_runs(store):
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id_1 = store.create_run(req)
    run_id_2 = store.create_run(req)

    assert run_id_1 != run_id_2
    runs = store.list_runs("strat_1")
    assert len(runs) == 2


def test_save_result_atomic_cleanup_on_failure(store, tmp_db):
    """If save_result fails, temp directory should be cleaned up."""
    req = BacktestRunRequest(
        strategy_id="strat_1",
        pine_id="pine_1",
        artifact_id="art_1",
        params_hash="ph_1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    run_id = store.create_run(req)

    # Force failure by passing invalid equity_curve (object without required attrs)
    class BadPoint:
        pass

    with pytest.raises(Exception):
        store.save_result(
            run_id, FakeResult(initial_capital=10000), [], equity_curve=[BadPoint()]
        )

    # Run should still be in running status (not done or failed)
    run = store.get_run(run_id)
    assert run.status == "running"
