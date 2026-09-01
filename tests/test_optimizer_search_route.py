from __future__ import annotations

from types import SimpleNamespace
import sys

import pytest
from pydantic import ValidationError

from openpine.gateway.routes.optimizer import optimizer_search
from openpine.gateway.schemas import OptimizerSearchRequest
from openpine.optimizer.isolated_runner import IsolatedOptimizerRunner
from openpine.registry.strategies import StrategyInstance
from tests.admission_helpers import make_sealed_artifact
from tests.rc4_fixtures import (
    admitted_manifest,
    canonical_bar_envelopes,
    execution_context,
)


def _artifact() -> dict[str, object]:
    return make_sealed_artifact()


def _strategy() -> StrategyInstance:
    return StrategyInstance(
        strategy_id="s1",
        name="Strategy",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_json='{"fee_buffer": 1}',
        params_hash="base-hash",
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
        semantic_profile="strict_5x",
    )


def test_optimizer_search_schema_rejects_invalid_or_duplicate_parameter_space() -> None:
    base = {
        "strategy_id": "s1",
        "from_time": "2026-01-01T00:00:00Z",
        "to_time": "2026-01-02T00:00:00Z",
        "trials": 3,
        "semantic_profile": "strict_5x",
    }
    parameter = {
        "name": "qty",
        "type": "int",
        "default": 1,
        "min": 1,
        "max": 3,
        "step": 1,
    }
    with pytest.raises(ValidationError, match="duplicate optimizer parameter names"):
        OptimizerSearchRequest(**base, parameters=[parameter, parameter])
    with pytest.raises(ValidationError, match="step must be positive"):
        OptimizerSearchRequest(**base, parameters=[{**parameter, "step": 0}])
    with pytest.raises(ValidationError):
        OptimizerSearchRequest(**base, parameters=[parameter], objective="unknown")


@pytest.mark.asyncio
async def test_optimizer_search_builds_real_isolated_run_and_returns_champion(
    monkeypatch, tmp_path
) -> None:
    from backtest_engine import Bar

    start_ms = 1_700_000_000_000
    bars = [
        Bar(
            time=start_ms + index,
            open=10,
            high=11,
            low=9,
            close=10,
            time_close=start_ms + index,
        )
        for index in range(4)
    ]
    captured: dict[str, object] = {"source_calls": 0}
    loaded: list[tuple[str, str]] = []

    def capture_source(source_id: str, artifact_id: str) -> bytes:
        captured["source_calls"] = int(captured["source_calls"]) + 1
        captured["source_identity"] = (source_id, artifact_id)
        return b"CAPTURED"

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source", capture_source
    )

    class Adapter:
        def allocate_optimization_id(self):
            return "opt-1"

        def start_optimization(self, config):
            captured["config"] = config
            return SimpleNamespace(optimization_id="opt-1", strategy_id="s1")

        def get_result(self, optimization_id):
            assert optimization_id == "opt-1"
            return SimpleNamespace(
                optimization_id="opt-1",
                strategy_id="s1",
                status="completed",
                trials_requested=3,
                trials_completed=3,
                best_params={"qty": 3},
                metrics={"net_profit": 15.0, "optimizer_adapter": "local"},
                trial_status_counts={"completed": 3, "failed": 0},
                trial_metadata=(
                    {
                        "id": 1,
                        "status": "completed",
                        "objective_value": 5.0,
                        "params_hash": "p1",
                        "result_content_hash": "r1",
                    },
                    {
                        "id": 3,
                        "status": "completed",
                        "objective_value": 15.0,
                        "params_hash": "p3",
                        "result_content_hash": "r3",
                    },
                ),
                uses_backtest_engine_path=True,
            )

    class Service:
        def __init__(self):
            self.adapter = Adapter()

    monkeypatch.setattr("openpine.optimizer.OptimizerService", Service)

    def persist(state, **kwargs):
        captured["job"] = kwargs
        return kwargs

    monkeypatch.setattr("openpine.gateway.side_effects.persist_gateway_job", persist)

    class JobStore:
        def mark_running(self, job_id, *, lease_owner, lease_deadline_utc_ms):
            captured["job_running"] = (job_id, lease_owner, lease_deadline_utc_ms)

        def mark_succeeded(self, job_id, *, lease_owner, result_artifact_refs=None):
            captured["job_lease_owner"] = lease_owner
            captured["job_terminal"] = (job_id, result_artifact_refs)

        def mark_failed(self, job_id, *, error_code, lease_owner=None):
            captured["job_failed"] = (job_id, error_code, lease_owner)

    def load_bars(query):
        loaded.append((query.instrument.symbol, query.timeframe.canonical))
        context = execution_context()
        return SimpleNamespace(
            bars=bars,
            canonical_bars=canonical_bar_envelopes(list(bars), context),
        )

    sealed_artifact = _artifact()
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        orchestrator=SimpleNamespace(load_bars=load_bars),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: sealed_artifact
        ),
        config=SimpleNamespace(data_dir=tmp_path),
        execution_context=execution_context(),
        admitted_manifest=admitted_manifest(),
        job_store=JobStore(),
    )

    response = await optimizer_search(
        OptimizerSearchRequest(
            strategy_id="s1",
            from_time=str(start_ms),
            to_time=str(start_ms + 3),
            trials=3,
            objective="net_profit",
            parameters=[
                {
                    "name": "qty",
                    "type": "int",
                    "default": 1,
                    "min": 1,
                    "max": 3,
                    "step": 1,
                }
            ],
            semantic_profile="strict_5x",
            mtf_series=[
                {"symbol": "BTCUSDT", "timeframe": "1D"},
                {"symbol": "ETHUSDT", "timeframe": "4h"},
            ],
        ),
        state,
    )

    assert response.status == "completed"
    assert response.optimization_id == "opt-1"
    assert response.champion is not None
    assert response.champion.params == {"qty": 3}
    assert response.champion.metrics == {"net_profit": 15.0}
    assert response.trials_completed == 3
    assert response.trials[1].result_content_hash == "r3"
    assert captured["source_calls"] == 0
    config = captured["config"]
    assert isinstance(config.runner, IsolatedOptimizerRunner)
    assert config.runner.source == str(sealed_artifact["python_code"]).encode("utf-8")
    assert config.runner.base_params == {"fee_buffer": 1}
    assert loaded == [
        ("BTCUSDT", "1m"),
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    ]
    assert {
        (item["symbol"], item["timeframe"])
        for item in config.runner.htf_bars
    } == {("BTCUSDT", "1D"), ("ETHUSDT", "4h")}
    assert config.data_query["mtf_series"] == [
        {"symbol": "BTCUSDT", "timeframe": "1D"},
        {"symbol": "ETHUSDT", "timeframe": "4h"},
    ]
    assert config.parameters[0]["name"] == "qty"
    assert config.optimization_id == "opt-1"
    assert config.data_query["from_time"] == start_ms
    assert captured["job"]["job_id"] == "opt-1"
    assert captured["job"]["semantic_profile"] == "strict_5x"
    assert "artifact:artifact-1" in captured["job"]["input_artifact_refs"]
    assert captured["job_terminal"] == ("opt-1", ["optimizer:opt-1"])
    assert captured["job_running"][0] == "opt-1"
    assert captured["job_lease_owner"] == captured["job_running"][1]
    assert "job_failed" not in captured


@pytest.mark.asyncio
async def test_optimizer_search_route_selects_real_isolated_champion(
    monkeypatch, tmp_path, job_store
) -> None:
    from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

    start_ms = 1_700_000_000_000
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    bars = [
        Bar(
            instrument=instrument,
            timeframe=timeframe,
            time=start_ms + index * 60_000,
            time_close=start_ms + (index + 1) * 60_000 - 1,
            open=10 + index * 5,
            high=11 + index * 5,
            low=9 + index * 5,
            close=10 + index * 5,
            volume=1,
            closed=True,
        )
        for index in range(4)
    ]
    market_context = execution_context(
        series_id=f"{instrument.serialize()}:1m",
        instrument_id=instrument.serialize(),
        exchange="binance",
        market="spot",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    source_calls: list[tuple[str, str]] = []
    sealed_artifact = _artifact()

    def capture_source(source_id: str, artifact_id: str) -> bytes:
        source_calls.append((source_id, artifact_id))
        return str(sealed_artifact["python_code"]).encode("utf-8")

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source", capture_source
    )
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        orchestrator=SimpleNamespace(
            load_bars=lambda query: SimpleNamespace(
                bars=bars,
                canonical_bars=canonical_bar_envelopes(
                    list(bars), market_context
                ),
            )
        ),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: sealed_artifact
        ),
        config=SimpleNamespace(data_dir=tmp_path),
        execution_context=market_context,
        admitted_manifest=admitted_manifest(),
        job_store=job_store,
    )

    response = await optimizer_search(
        OptimizerSearchRequest(
            strategy_id="s1",
            from_time=str(start_ms),
            to_time=str(start_ms + 3 * 60_000),
            trials=3,
            objective="net_profit",
            parameters=[
                {
                    "name": "qty",
                    "type": "int",
                    "default": 1,
                    "min": 1,
                    "max": 3,
                    "step": 1,
                }
            ],
            semantic_profile="strict_5x",
        ),
        state,
    )

    assert response.status == "completed", response.model_dump()
    assert response.trials_completed == 3
    assert response.champion is not None
    assert response.champion.params == {"qty": 1}
    assert response.champion.metrics["net_profit"] == 0.0
    assert list((tmp_path / "optimizer").glob("opt_*/trials.jsonl"))
    assert "artifact_uri" not in response.model_dump()
    assert source_calls == []
    assert not any(name.startswith("openpine_generated_") for name in sys.modules)
