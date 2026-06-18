from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.storage import BacktestResultStore, BacktestRunRequest, MigrationRunner, SQLiteStorage
from openpine.storage.backtest_dto import ARTIFACT_TYPE_REPORT_JSON, BacktestMetricsSummary
from openpine.telegram_commands import (
    TelegramCommandError,
    catalog_families,
    confirm_delete_keyboard,
    data_jobs_keyboard,
    generate_help_text,
    home_menu_keyboard,
    inline_keyboard,
    map_callback_data,
    map_telegram_command,
    pine_list_keyboard,
    reports_keyboard,
    risk_keyboard,
    strategy_actions_keyboard,
    strategy_list_keyboard,
)


def _storage(tmp_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def _request() -> BacktestRunRequest:
    return BacktestRunRequest(
        strategy_id="strat",
        pine_id="pine",
        artifact_id="art",
        params_hash="ph",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="15m",
        from_time=1,
        to_time=2,
        warmup_bars=3,
    )


def test_backtest_store_delete_metrics_and_artifact_edges(tmp_path: Path):
    with _storage(tmp_path) as storage:
        store = BacktestResultStore(storage)
        run_id = store.create_run(_request())
        store.mark_running(run_id)
        row = store.get_run(run_id)
        assert row is not None and row.status == "running" and row.warmup_bars == 3
        assert store.get_run("missing") is None
        assert store.get_latest_run("missing") is None
        assert store.list_runs("missing") == []
        assert store.delete_run("missing") is False
        # metrics_json fallback path.
        storage.execute("ALTER TABLE backtest_runs ADD COLUMN metrics_json TEXT")
        storage.execute("UPDATE backtest_runs SET metrics_json = ? WHERE run_id = ?", (json.dumps({"net": 1}), run_id))
        storage.commit()
        metrics = store.get_metrics(run_id)
        assert metrics is not None and metrics["net"] == 1
        storage.execute("UPDATE backtest_runs SET metrics_json = ? WHERE run_id = ?", ("{bad", run_id))
        storage.commit()
        fallback_metrics = store.get_metrics(run_id)
        assert fallback_metrics is not None
        assert fallback_metrics["trades_total"] == 0
        assert fallback_metrics["total_trades"] == 0
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"metrics": {"x": 2}}), encoding="utf-8")
        storage.execute(
            "INSERT INTO backtest_artifacts(artifact_row_id, run_id, strategy_id, artifact_type, path, format, row_count, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("ar1", run_id, "strat", ARTIFACT_TYPE_REPORT_JSON, str(report), "json", None, 9),
        )
        storage.commit()
        merged_metrics = store.get_metrics(run_id)
        assert merged_metrics is not None
        assert merged_metrics["metrics"]["x"] == 2
        assert merged_metrics["metrics"]["trades_total"] == 0
        assert merged_metrics["metrics"]["total_trades"] == 0
        report.write_text("{bad", encoding="utf-8")
        fallback_metrics = store.get_metrics(run_id)
        assert fallback_metrics is not None
        assert fallback_metrics["trades_total"] == 0
        assert fallback_metrics["total_trades"] == 0
        artifact_dir = tmp_path / "data" / "strat" / run_id
        store._data_dir = tmp_path / "data"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "x.txt").write_text("x", encoding="utf-8")
        assert store.delete_run(run_id) is True
        assert not artifact_dir.exists()


def test_backtest_trade_exit_prices_are_persisted(tmp_path: Path):
    with _storage(tmp_path) as storage:
        store = BacktestResultStore(storage)
        run_id = store.create_run(_request())
        trade = SimpleNamespace(
            id="entry-1",
            exit_id="TP1:L",
            direction="long",
            entry_time=1,
            exit_time=2,
            entry_price=100.0,
            exit_price=110.0,
            stop_price=95.0,
            take_profit_price=110.0,
            qty=1.0,
            profit=10.0,
            profit_percent=10.0,
            commission_entry=0.0,
            commission_exit=0.0,
            bars_held=1,
        )

        store._insert_trade_db_rows(run_id=run_id, strategy_id="strat", trades=[trade], now=3)

        row = store.list_trades(run_id)[0]
        assert row.stop_price == 95.0
        assert row.take_profit_price == 110.0
        assert storage.execute(
            "SELECT stop_price, take_profit_price FROM backtest_trades WHERE run_id = ?",
            (run_id,),
        ).fetchone() == (95.0, 110.0)


def test_backtest_store_artifact_records_and_publish_failures(tmp_path: Path):
    with _storage(tmp_path) as storage:
        store = BacktestResultStore(storage)
        tmp_dir = tmp_path / "tmp"; tmp_dir.mkdir()
        run_dir = tmp_path / "run"
        with pytest.raises(RuntimeError):
            store._publish_result_artifacts(tmp_dir=tmp_dir, run_dir=run_dir, artifact_paths={"missing": tmp_path / "missing.parquet"})
        existing = run_dir; existing.mkdir(); (existing / "old").write_text("old", encoding="utf-8")
        artifact = tmp_dir / "report.json"; artifact.write_text("{}", encoding="utf-8")
        store._publish_result_artifacts(tmp_dir=tmp_dir, run_dir=run_dir, artifact_paths={"report": artifact})
        assert (run_dir / "report.json").exists() and not (run_dir / "old").exists()
        assert BacktestResultStore._result_json_payload(BacktestMetricsSummary(net_profit=1, trades_total=2))["total_trades"] == 2
        payload = BacktestResultStore._report_payload("run", "strat", SimpleNamespace(symbol="BTC", timeframe="1D"), BacktestMetricsSummary(net_profit=1), run_dir, has_plot_outputs=True, now=123)
        assert payload["plot_outputs_path"].endswith("plot_outputs.parquet")
        class PlotObj:
            bar_time = 10; bar_index = 1; value = 2.5; title = "p"
        assert BacktestResultStore._plot_records([PlotObj()])[0]["value"] == 2.5


def test_telegram_catalog_mapping_keyboards_and_errors():
    families = catalog_families()
    assert "strategy" in families and "data" in families
    assert map_telegram_command("/risk") == ["risk", "status"]
    assert map_telegram_command("/risk@bot") == ["risk", "status"]
    assert map_telegram_command("/help strategy") == ["help", "strategy"]
    assert map_telegram_command("/op strategy list") == ["strategy", "list"]
    assert map_telegram_command("/strategy_error_clear sid --force") == ["strategy", "error", "sid", "clear", "--force"]
    for bad in ("", "not-command", "/unknown", "/op", "/strategy_error_clear"):
        with pytest.raises(TelegramCommandError):
            map_telegram_command(bad)
    with pytest.raises(TelegramCommandError):
        map_telegram_command("/op 'unterminated")
    assert map_callback_data("op:home") == []
    assert map_callback_data("op:menu:data_jobs") == []
    assert map_callback_data("op:strategy:paper_start:sid") == ["strategy", "paper", "sid", "start"]
    assert map_callback_data("op:strategy:live_enable:sid") == ["strategy", "live", "sid", "enable"]
    assert map_callback_data("op:strat:delete:sid") == ["strategy", "remove", "sid"]
    assert map_callback_data("op:pine:activate:src") == ["pine", "artifacts", "src"]
    assert map_callback_data("op:reports:show:data") == ["reports", "show", "data"]
    for bad in ("bad", "op:bad", "op:strategy:bad:sid", "op:strat:bad:sid", "op:pine:bad:src", "op:pine:show:"):
        with pytest.raises(TelegramCommandError):
            map_callback_data(bad)
    assert "OpenPine Telegram commands" in generate_help_text()
    assert "strategy:" in generate_help_text("strategy")
    with pytest.raises(TelegramCommandError):
        generate_help_text("missing")
    keyboards = [
        home_menu_keyboard(), risk_keyboard(), data_jobs_keyboard(), reports_keyboard(),
        strategy_actions_keyboard("sid"), confirm_delete_keyboard("sid"),
        strategy_list_keyboard([
            {"strategy_id": "s1", "name": "A", "status": "paused"},
            {"strategy_id": "s2", "name": "B", "status": "live"},
            {"strategy_id": "s3", "name": "C", "status": "paper"},
            {"strategy_id": "s4", "name": "D", "status": "running"},
        ]),
        pine_list_keyboard([
            {"id": "p1", "name": "P", "active_artifact_id": "a"},
            {"id": "p2", "name": "Q", "active_artifact_id": None},
        ]),
        inline_keyboard(((SimpleNamespace(text="T", callback_data="c"),),)),
    ]
    assert all("inline_keyboard" in keyboard for keyboard in keyboards)
    with pytest.raises(TelegramCommandError):
        confirm_delete_keyboard("bad:id")
