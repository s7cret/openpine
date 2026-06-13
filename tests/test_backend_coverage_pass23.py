from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from openpine.gateway.routes import backtest as routes
from openpine.gateway.schemas import BacktestEstimateResponse, BacktestRunRequest
from openpine.registry.strategies import StrategyInstance


def _strategy(strategy_id: str = "s1", *, missing_artifact: bool = False) -> StrategyInstance:
    return StrategyInstance(
        strategy_id=strategy_id,
        name="Strategy",
        pine_id="" if missing_artifact else "pine",
        artifact_id="" if missing_artifact else "artifact",
        params_json="{}",
        params_hash="ph",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1m",
        mode="paper",
        status="paused",
        enabled=False,
        created_at=1,
        updated_at=2,
    )


class Registry:
    def __init__(self) -> None:
        self.items = {"s1": _strategy("s1"), "bad": _strategy("bad", missing_artifact=True)}
    def get_strategy(self, strategy_id):
        if strategy_id not in self.items: raise KeyError(strategy_id)
        return self.items[strategy_id]


class Store:
    def __init__(self) -> None:
        self.created: list[object] = []
        self.deleted = {"gone": False, "run": True}
        self.run = SimpleNamespace(run_id="run", strategy_id="s1", status="completed", started_at=1, finished_at=2, symbol="BTCUSDT", timeframe="1m", from_time=10, to_time=20, bars_processed=7)
        self.trade = SimpleNamespace(trade_id="t1", entry_time=1, exit_time=2, direction="long", entry_price=1.0, exit_price=2.0, qty=3.0, net_pnl=4.0)
        self.artifacts = [SimpleNamespace(artifact_type="equity_curve", path="equity.parquet"), SimpleNamespace(artifact_type="plot_outputs", path="plots.parquet"), SimpleNamespace(artifact_type="bar_outputs", path="bars.parquet"), SimpleNamespace(artifact_type="report_md", path="report.md")]
    def create_run(self, req): self.created.append(req); return "run"
    def get_run(self, run_id): return self.run if run_id == "run" else None
    def get_metrics(self, run_id): return {"metrics": {"total_trades": 2}}
    def list_runs(self, strategy_id, limit=1000): return [self.run]
    def delete_run(self, run_id): return self.deleted.get(run_id, False)
    def list_trades(self, run_id): return [self.trade]
    def list_artifacts(self, run_id): return self.artifacts


class Storage:
    def __init__(self) -> None: self.columns = set(); self.executed = []
    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if sql.startswith("PRAGMA"):
            rows = [(0, col) for col in sorted(self.columns)]
        else:
            rows = []
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)
    def commit(self): pass


@pytest.mark.asyncio
async def test_backtest_routes_run_outputs_actions_and_progress(monkeypatch, tmp_path):
    store = Store(); storage = Storage(); registry = Registry()
    state = SimpleNamespace(strategy_registry=registry, backtest_store=store, storage=storage, backtest_cancel_requests=set())

    estimate = BacktestEstimateResponse(strategy_id="s1", symbol="BTCUSDT", timeframe="1m", requested_from=1, requested_to=2, effective_from=1, effective_to=2, earliest_available=0, adjusted=True, estimated_bars=1, estimated_pages=1)
    monkeypatch.setattr(routes, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: estimate)
    body = BacktestRunRequest(strategy_id="s1", from_time="1", to_time="2", warmup_bars=5, capture_plots=True)
    resp = await routes.run_backtest(body, BackgroundTasks(), state)
    assert resp.run_id == "run" and store.created[0].warmup_bars == 5
    with pytest.raises(HTTPException): await routes.run_backtest(BacktestRunRequest(strategy_id="s1", from_time="2", to_time="1"), BackgroundTasks(), state)
    with pytest.raises(HTTPException): await routes.run_backtest(BacktestRunRequest(strategy_id="missing", from_time="1", to_time="2"), BackgroundTasks(), state)
    with pytest.raises(HTTPException): await routes.run_backtest(BacktestRunRequest(strategy_id="bad", from_time="1", to_time="2"), BackgroundTasks(), state)
    monkeypatch.setattr(routes, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: BacktestEstimateResponse(strategy_id="s1", symbol="BTCUSDT", timeframe="1m", requested_from=1, requested_to=2, effective_from=5, effective_to=5, earliest_available=0, adjusted=False, estimated_bars=0, estimated_pages=0))
    with pytest.raises(HTTPException): await routes.run_backtest(body, BackgroundTasks(), state)

    detail = await routes.get_run("run", state)
    assert detail.metrics["trades_total"] == 2 and detail.version == 1
    with pytest.raises(HTTPException): await routes.get_run("missing", state)
    await routes.delete_run("run", state)
    with pytest.raises(HTTPException): await routes.delete_run("missing", state)
    assert (await routes.run_action("run", "cancel", state))["accepted"] is False
    with pytest.raises(HTTPException): await routes.run_action("run", "bad", state)
    store.run.status = "running"
    assert (await routes.run_action("run", "cancel", state))["accepted"] is True
    with pytest.raises(HTTPException): await routes.run_action("missing", "cancel", state)
    trades = await routes.get_run_trades("run", state)
    assert trades[0].net_profit == 4.0
    with pytest.raises(HTTPException): await routes.get_run_trades("missing", state)

    routes.ws_manager.update_progress("run", "backtest", "running", 50.0, "Bars: 1,000/2,000")
    assert (await routes.get_progress("run")).bars_processed == 1000
    routes.ws_manager.update_progress("run2", "backtest", "running", 25.0, "x", detail={"bars_processed": 2, "total_bars": 8})
    assert (await routes.get_progress("run2")).total_bars == 8
    assert await routes.get_progress("missing") is None
    assert (await routes.get_progress_detail("run"))["operation_id"] == "run"

    monkeypatch.setattr(routes, "_read_parquet_as_csv", lambda path: f"csv:{path}")
    assert (await routes.get_run_equity("run", state))["data"] == "csv:equity.parquet"
    assert (await routes.get_run_plots("run", state))["data"] == "csv:plots.parquet"
    assert (await routes.get_run_bar_outputs("run", state))["data"] == "csv:bars.parquet"
    report = tmp_path / "report.md"; report.write_text("hello", encoding="utf-8"); store.artifacts[-1].path = str(report)
    assert (await routes.get_run_report("run", state))["data"] == "hello"
    exported = await routes.export_run("run", state)
    assert exported["trades"] and exported["artifacts"]
    for func in (routes.get_run_equity, routes.get_run_plots, routes.get_run_bar_outputs, routes.get_run_report, routes.export_run):
        with pytest.raises(HTTPException): await func("missing", state)
    store.artifacts = []
    with pytest.raises(HTTPException): await routes.get_run_equity("run", state)

    routes._save_backtest_data_fingerprint(state, "run", "fp")
    assert "data_fingerprint" in storage.columns or storage.executed
    assert routes._normalize_metrics_payload({"trades_total": 3})["total_trades"] == 3
    assert routes._normalize_metrics_payload(None) is None
