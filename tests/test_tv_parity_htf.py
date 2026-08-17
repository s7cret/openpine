from __future__ import annotations

from types import SimpleNamespace

from openpine.gateway.routes.tv_parity import _run_isolated_tv_replay


def test_isolated_tv_replay_forwards_confirmed_htf_bars() -> None:
    seen: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    result = _run_isolated_tv_replay(
        Adapter(),
        b"STAMPED",
        [],
        object(),
        {},
        None,
        htf_bars=htf_bars,
    )
    assert result.ok is True
    assert seen["htf_bars"] == htf_bars


def test_isolated_tv_replay_stamps_confirmed_provider_htf_bars() -> None:
    seen: dict[str, object] = {}
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    result = _run_isolated_tv_replay(
        Adapter(),
        b"STAMPED",
        bars,
        SimpleNamespace(symbol="BTCUSDT", timeframe="1m"),
        {},
        None,
    )
    assert result.ok is True
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]


def test_isolated_tv_replay_does_not_invent_time_close() -> None:
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            seen["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(ok=True)

    result = _run_isolated_tv_replay(
        Adapter(),
        b"STAMPED",
        [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
        SimpleNamespace(symbol="BTCUSDT", timeframe="1m"),
        {},
        None,
    )
    assert result.ok is True
    assert seen["htf_bars"] is None
