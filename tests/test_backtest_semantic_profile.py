from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from openpine.gateway.routes import backtest as routes
from openpine.gateway.schemas import BacktestEstimateResponse, BacktestRunRequest
from openpine.registry.strategies import StrategyInstance


def _strategy(*, semantic_profile: str | None = None) -> StrategyInstance:
    return StrategyInstance(
        strategy_id="s1",
        name="Strategy",
        pine_id="pine",
        artifact_id="artifact",
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
        semantic_profile=semantic_profile,
    )


class _JobStore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs


class _BacktestStore:
    def create_run(self, req):
        return "run-1"


@pytest.mark.asyncio
async def test_run_backtest_persists_admitted_semantic_profile(monkeypatch) -> None:
    strategy = _strategy(semantic_profile="strict_5x")
    jobs = _JobStore()
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        backtest_store=_BacktestStore(),
        storage=SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(fetchall=lambda: [])),
        backtest_cancel_requests=set(),
        job_store=jobs,
        config=SimpleNamespace(live_enabled=True),
    )
    estimate = BacktestEstimateResponse(
        strategy_id="s1",
        symbol="BTCUSDT",
        timeframe="1m",
        requested_from=1,
        requested_to=2,
        effective_from=1,
        effective_to=2,
        earliest_available=0,
        adjusted=False,
        estimated_bars=1,
        estimated_pages=1,
    )
    monkeypatch.setattr(routes, "_estimate_backtest_market_data", lambda *a, **k: estimate)
    monkeypatch.setattr(routes, "ws_manager", SimpleNamespace(update_progress=lambda *a, **k: None))
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        BackgroundTasks,
        "add_task",
        lambda self, fn, *args, **kwargs: scheduled.append((fn, args, kwargs)),
    )
    body = BacktestRunRequest(strategy_id="s1", from_time="1", to_time="2")
    resp = await routes.run_backtest(body, BackgroundTasks(), state)
    assert resp.run_id == "run-1"
    refs = list(jobs.created[0]["input_artifact_refs"])
    assert "semantic_profile:strict_5x" in refs
    assert "semantic_profile:legacy_4x" not in refs
    _fn, args, kwargs = scheduled[0]
    assert kwargs.get("semantic_profile") == "strict_5x" or "strict_5x" in args

