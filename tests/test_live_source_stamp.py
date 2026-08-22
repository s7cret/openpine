from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig
from tests.admission_helpers import STACK_HASH, make_deployment_identity


class _Strategy:
    strategy_id = "s1"
    artifact_id = "a1"
    params_hash = "h"
    exchange = "BINANCE"
    market_type = "SPOT"
    symbol = "btcusdt"
    timeframe = "1m"
    pine_id = "p1"
    name = "Strategy"
    enabled = True
    status = "running"
    mode = "live"
    semantic_profile = "strict_5x"


def _bar(t: int = 0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, 1.0, 1.0, 1.0, 1.0, 1.0, True)


def _series(start: int, end: int) -> BarSeries:
    bars = (_bar(start), _bar(end - 60_000))
    query = BarQuery(bars[0].instrument, bars[0].timeframe, start, end, gap_policy="allow_with_metadata")
    coverage = CoverageReport(start, end, bars[0].time, bars[-1].time_close, source_mix=("test",))
    return BarSeries(query, bars, coverage)


def test_live_runner_fetches_two_explicit_mtf_series() -> None:
    loaded: list[tuple[str, str]] = []

    def load_bars(query):
        key = (query.instrument.symbol, query.timeframe.canonical)
        loaded.append(key)
        duration = query.timeframe.duration_ms or 60_000
        bar = Bar(
            query.instrument,
            query.timeframe,
            0,
            duration - 1,
            2,
            3,
            1,
            2,
            1,
            True,
        )
        return SimpleNamespace(bars=(bar,))

    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=load_bars),
        state_store=None,
    )
    runner.set_strategy_mtf_series(
        "s1",
        [
            {"symbol": "BTCUSDT", "timeframe": "1D"},
            {"symbol": "ETHUSDT", "timeframe": "4h"},
        ],
    )

    stamped = runner._confirmed_htf_bars(_Strategy(), [_bar(0)])

    assert loaded == [("BTCUSDT", "1D"), ("ETHUSDT", "4h")]
    assert {(item["symbol"], item["timeframe"]) for item in stamped} == {
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    }


def test_start_live_sets_per_strategy_htf_timeframe(monkeypatch) -> None:
    import asyncio
    import time

    from openpine.gateway.routes import trading
    from openpine.gateway.schemas import LiveStartRequest
    from openpine.live_preview import make_live_preview

    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)
    monkeypatch.setattr(
        "openpine.gateway.routes.activation_guard.require_worker_ready",
        lambda state: None,
    )
    runner = LiveStrategyRunner(RunnerConfig(lookback_bars=2), state_store=None)
    strategy = _Strategy()
    registry = SimpleNamespace(
        get_strategy=lambda sid: strategy,
        activate_strategy=lambda *a, **k: None,
    )
    state = SimpleNamespace(
        config=SimpleNamespace(live_enabled=True),
        strategy_registry=registry,
        _live_runner=runner,
        admission_identity=make_deployment_identity(),
    )
    preview = make_live_preview("s1", now_ms=int(time.time() * 1000), stack_id=STACK_HASH)
    result = asyncio.run(
        trading.start_live(
            LiveStartRequest(
                strategy_id="s1",
                preview_hash=preview["preview_hash"],
                confirmation="LIVE",
                idempotency_key="live-htf",
                expires_at_utc_ms=preview["expires_at_utc_ms"],
                semantic_profile="strict_5x",
                htf_timeframe="1D",
            ),
            state,
        )
    )
    assert result.status == "running"
    assert runner._htf_timeframe_by_strategy["s1"] == "1D"
    preview = make_live_preview("s1", now_ms=int(time.time() * 1000), stack_id=STACK_HASH)
    asyncio.run(
        trading.start_live(
            LiveStartRequest(
                strategy_id="s1",
                preview_hash=preview["preview_hash"],
                confirmation="LIVE",
                idempotency_key="live-htf-clear",
                expires_at_utc_ms=preview["expires_at_utc_ms"],
                semantic_profile="strict_5x",
            ),
            state,
        )
    )
    assert "s1" not in runner._htf_timeframe_by_strategy


def test_start_live_persists_multi_series_when_worker_is_delegated(monkeypatch) -> None:
    import asyncio
    import time

    from openpine.gateway.routes import trading
    from openpine.gateway.schemas import LiveStartRequest
    from openpine.live_preview import make_live_preview

    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)
    monkeypatch.setattr(
        "openpine.gateway.routes.activation_guard.require_worker_ready",
        lambda state: None,
    )
    strategy = _Strategy()
    persisted: list[tuple[str, list[dict[str, str]]]] = []
    events: list[str] = []
    registry = SimpleNamespace(
        get_strategy=lambda sid: strategy,
        activate_strategy=lambda *a, **k: events.append("activate"),
        set_mtf_series=lambda sid, series: (
            events.append("persist"),
            persisted.append((sid, series)),
        ),
    )
    state = SimpleNamespace(
        config=SimpleNamespace(live_enabled=True),
        strategy_registry=registry,
        _live_runner=None,
        admission_identity=make_deployment_identity(),
    )
    preview = make_live_preview("s1", now_ms=int(time.time() * 1000), stack_id=STACK_HASH)

    result = asyncio.run(
        trading.start_live(
            LiveStartRequest(
                strategy_id="s1",
                preview_hash=preview["preview_hash"],
                confirmation="LIVE",
                idempotency_key="live-multi-mtf",
                expires_at_utc_ms=preview["expires_at_utc_ms"],
                semantic_profile="strict_5x",
                mtf_series=[
                    {"symbol": "btcusdt", "timeframe": "1d"},
                    {"symbol": "ethusdt", "timeframe": "4H"},
                ],
            ),
            state,
        )
    )

    assert result.status == "running"
    assert events == ["persist", "activate"]
    assert persisted == [
        (
            "s1",
            [
                {"symbol": "BTCUSDT", "timeframe": "1D"},
                {"symbol": "ETHUSDT", "timeframe": "4h"},
            ],
        )
    ]
