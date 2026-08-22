from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway import server
from openpine.gateway.routes import trading


def test_live_runner_contains_no_synthetic_execution_or_regex_risk_path() -> None:
    from pathlib import Path

    from openpine.gateway.live_runner import LiveStrategyRunner

    text = Path("openpine/gateway/live_runner.py").read_text(encoding="utf-8")
    for name in (
        "_run_mini_backtest",
        "_extract_new_orders",
        "_process_orders",
        "_extract_percent_input",
        "_strategy_risk_percents",
        "_attach_risk_prices",
    ):
        assert not hasattr(LiveStrategyRunner, name)
    assert '"filled"' not in text
    assert "tpPct" not in text
    assert "slPct" not in text


@pytest.mark.asyncio
async def test_live_runner_fails_closed_before_advancing_bar_state() -> None:
    from openpine.gateway.live_runner import (
        CanonicalOrderRouterRequired,
        LiveStrategyRunner,
        RunnerConfig,
    )

    strategy = SimpleNamespace(
        strategy_id="s1",
        artifact_id="artifact",
        params_hash="params",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    runner = LiveStrategyRunner(RunnerConfig(), state_store=None)

    with pytest.raises(CanonicalOrderRouterRequired, match="OrderRouter"):
        await runner._process_strategy(strategy, 120_001)

    assert runner._strategy_states["s1"].last_bar_time_ms == 0


def test_rc_live_runner_stays_disabled_even_when_config_and_env_request_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "1")
    state = SimpleNamespace(config=SimpleNamespace(live_enabled=True))

    assert server._live_runner_requested(state) is False


@pytest.mark.asyncio
async def test_rc_live_start_is_blocked_before_confirmation_or_mutation() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await trading.start_live(SimpleNamespace(), SimpleNamespace())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "LIVE_RC_BLOCKED"


@pytest.mark.asyncio
async def test_rc_live_admission_is_explicitly_denied() -> None:
    result = await trading.live_admission()

    assert result == {
        "admitted": False,
        "code": "LIVE_RC_BLOCKED",
        "message": "live execution is disabled for this release candidate",
        "mutating": False,
    }
