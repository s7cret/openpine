from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openpine.gateway.schemas import BacktestRunRequest
from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_indicator
from openpine.runtime.mtf import (
    confirmed_mtf_bars_for_requests,
    parse_mtf_series_args,
)


MULTI_SERIES_SOURCE = b"""
from pinelib.request.security import security

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime

    def _process_bar(self, bar, bar_index=None):
        for title, symbol, timeframe in (
            ("btc_daily", "BTCUSDT", "1D"),
            ("eth_four_hour", "ETHUSDT", "4h"),
        ):
            value = security(
                symbol,
                timeframe,
                lambda request_rt: request_rt.close,
                runtime=self.rt,
                state_id=title,
                gaps="barmerge.gaps_off",
                lookahead="barmerge.lookahead_off",
            )
            self.rt.plot_recorder.record_plot(
                int(bar.time), int(bar_index or 0), value, title
            )
"""


def _payload(symbol: str, timeframe: str, duration_ms: int, values: list[int]):
    return [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "time": index * duration_ms,
            "time_close": (index + 1) * duration_ms - 1,
            "open": value,
            "high": value,
            "low": value,
            "close": value,
            "volume": 1,
        }
        for index, value in enumerate(values)
    ]


def test_isolated_indicator_supports_two_requested_mtf_series() -> None:
    chart = _payload("BTCUSDT", "15m", 900_000, [1, 1, 1, 1])
    for index, bar in enumerate(chart):
        bar["time"] = 84_600_000 + index * 900_000
        bar["time_close"] = bar["time"] + 899_999
    mtf_bars = [
        *_payload("BTCUSDT", "1D", 86_400_000, [100, 200]),
        *_payload("ETHUSDT", "4h", 14_400_000, [10, 20, 30, 40, 50, 60, 70]),
    ]

    result = run_isolated_indicator(
        MULTI_SERIES_SOURCE,
        chart,
        semantic_profile="strict_5x",
        htf_bars=mtf_bars,
    )

    by_title: dict[str, list[object]] = {}
    for _time, _index, value, title in result.plots:
        by_title.setdefault(title, []).append(value)
    assert by_title == {
        "btc_daily": ["na", "na", "200", "200"],
        "eth_four_hour": ["na", "60", "70", "70"],
    }


def test_confirmed_mtf_fetches_each_explicit_series_once() -> None:
    chart = [
        SimpleNamespace(
            time=0,
            time_close=899_999,
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
        )
    ]
    loaded: list[tuple[str, str]] = []

    def load(symbol: str, timeframe: str):
        loaded.append((symbol, timeframe))
        duration = 86_400_000 if timeframe == "1D" else 14_400_000
        return [
            SimpleNamespace(
                time=0,
                time_close=duration - 1,
                open=2,
                high=3,
                low=1,
                close=2,
                volume=4,
            )
        ]

    stamped = confirmed_mtf_bars_for_requests(
        chart_bars=chart,
        chart_symbol="BTCUSDT",
        chart_timeframe="15m",
        requests=[
            {"symbol": "btcusdt", "timeframe": "1d"},
            {"symbol": "ethusdt", "timeframe": "4h"},
        ],
        load_bars=load,
    )

    assert loaded == [("BTCUSDT", "1D"), ("ETHUSDT", "4h")]
    assert {(bar["symbol"], bar["timeframe"]) for bar in stamped} == {
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    }


def test_confirmed_mtf_fails_when_any_requested_series_is_missing() -> None:
    with pytest.raises(IsolatedRunError, match="ETHUSDT 4h.*no confirmed bars"):
        confirmed_mtf_bars_for_requests(
            chart_bars=[],
            chart_symbol="BTCUSDT",
            chart_timeframe="15m",
            requests=[{"symbol": "ETHUSDT", "timeframe": "4h"}],
            load_bars=lambda symbol, timeframe: [],
        )


def test_backtest_mtf_schema_canonicalizes_and_rejects_ambiguous_requests() -> None:
    request = BacktestRunRequest(
        strategy_id="s1",
        from_time="2026-01-01T00:00:00Z",
        to_time="2026-01-02T00:00:00Z",
        semantic_profile="strict_5x",
        mtf_series=[
            {"symbol": "btcusdt", "timeframe": "1d"},
            {"symbol": "ethusdt", "timeframe": "4h"},
        ],
    )
    assert [item.model_dump() for item in request.mtf_series] == [
        {"symbol": "BTCUSDT", "timeframe": "1D"},
        {"symbol": "ETHUSDT", "timeframe": "4h"},
    ]

    with pytest.raises(ValidationError, match="cannot be combined"):
        BacktestRunRequest(
            strategy_id="s1",
            from_time="2026-01-01T00:00:00Z",
            to_time="2026-01-02T00:00:00Z",
            semantic_profile="strict_5x",
            htf_timeframe="1D",
            mtf_series=[{"symbol": "BTCUSDT", "timeframe": "4h"}],
        )
    with pytest.raises(ValidationError, match="duplicate MTF series"):
        BacktestRunRequest(
            strategy_id="s1",
            from_time="2026-01-01T00:00:00Z",
            to_time="2026-01-02T00:00:00Z",
            semantic_profile="strict_5x",
            mtf_series=[
                {"symbol": "btcusdt", "timeframe": "1d"},
                {"symbol": "BTCUSDT", "timeframe": "1D"},
            ],
        )
    with pytest.raises(ValidationError, match="symbol is required"):
        BacktestRunRequest(
            strategy_id="s1",
            from_time="2026-01-01T00:00:00Z",
            to_time="2026-01-02T00:00:00Z",
            semantic_profile="strict_5x",
            mtf_series=[{"symbol": "   ", "timeframe": "1D"}],
        )


def test_backtest_helper_loads_two_explicit_symbols() -> None:
    from openpine.gateway.routes import backtest

    chart = [
        SimpleNamespace(
            time=0,
            time_close=899_999,
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
        )
    ]
    loaded: list[tuple[str, str]] = []

    def load_bars(query):
        loaded.append((query.instrument.symbol, query.timeframe.canonical))
        duration = query.timeframe.duration_ms or 60_000
        return SimpleNamespace(
            bars=[
                SimpleNamespace(
                    time=0,
                    time_close=duration - 1,
                    open=2,
                    high=3,
                    low=1,
                    close=2,
                    volume=1,
                )
            ]
        )

    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
    )
    stamped = backtest._confirmed_htf_bars_for_backtest(
        chart,
        strategy=strategy,
        requested_timeframe=None,
        mtf_series=[
            {"symbol": "BTCUSDT", "timeframe": "1D"},
            {"symbol": "ETHUSDT", "timeframe": "4h"},
        ],
        load_bars=load_bars,
        from_ms=0,
        to_ms=900_000,
    )

    assert loaded == [("BTCUSDT", "1D"), ("ETHUSDT", "4h")]
    assert {(item["symbol"], item["timeframe"]) for item in stamped} == {
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    }


def test_replay_helper_loads_two_explicit_symbols() -> None:
    from marketdata_provider.contracts import InstrumentKey
    from openpine.gateway.routes import strategies

    chart = [
        SimpleNamespace(
            time=0,
            time_close=899_999,
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
        )
    ]
    loaded: list[tuple[str, str]] = []

    def load_bars(query):
        loaded.append((query.instrument.symbol, query.timeframe.canonical))
        duration = query.timeframe.duration_ms or 60_000
        return [
            SimpleNamespace(
                time=0,
                time_close=duration - 1,
                open=2,
                high=3,
                low=1,
                close=2,
                volume=1,
            )
        ]

    stamped = strategies._confirmed_htf_bars_for_replay(
        chart,
        symbol="BTCUSDT",
        chart_timeframe="15m",
        requested_timeframe=None,
        mtf_series=[
            {"symbol": "BTCUSDT", "timeframe": "1D"},
            {"symbol": "ETHUSDT", "timeframe": "4h"},
        ],
        load_bars=load_bars,
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        start_ms=0,
        end_ms=900_000,
    )

    assert loaded == [("BTCUSDT", "1D"), ("ETHUSDT", "4h")]
    assert {(item["symbol"], item["timeframe"]) for item in stamped} == {
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    }


def test_repeatable_mtf_cli_values_are_explicit_and_canonical() -> None:
    parsed = parse_mtf_series_args(("btcusdt:1d", "ethusdt:4H"))
    assert [item.to_dict() for item in parsed] == [
        {"symbol": "BTCUSDT", "timeframe": "1D"},
        {"symbol": "ETHUSDT", "timeframe": "4h"},
    ]
    with pytest.raises(ValueError, match="SYMBOL:TIMEFRAME"):
        parse_mtf_series_args(("BTCUSDT",))
