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
