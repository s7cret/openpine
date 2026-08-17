from __future__ import annotations

import inspect
from types import SimpleNamespace

from openpine.gateway.routes import strategies
from openpine.gateway.routes.strategies import _run_isolated_strategy_replay


def test_isolated_strategy_replay_forwards_confirmed_htf_bars() -> None:
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
            return SimpleNamespace(ok=True, bars_processed=1)

    result = _run_isolated_strategy_replay(
        Adapter(),
        b"STAMPED",
        [],
        object(),
        htf_bars=htf_bars,
    )
    assert result.ok is True
    assert seen["htf_bars"] == htf_bars


def test_strategy_replay_wires_isolated_htf_bars() -> None:
    source = inspect.getsource(strategies.strategy_replay)
    assert "_run_isolated_strategy_replay" in source
    assert "htf_bars=" in source
