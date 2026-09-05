from __future__ import annotations

import pytest

from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig
from openpine.runtime.isolated_run import IsolatedRunError


def test_backtest_run_config_has_no_silent_legacy_default() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    assert not config.semantic_profile


def test_run_isolated_rejects_default_backtest_run_config() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        BacktestEngineAdapter().run_isolated(b"VALUE = 1\n", [], config)


def test_adapter_run_rejects_missing_semantic_profile() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        BacktestEngineAdapter().run(type("Strategy", (), {}), [], config)


def test_adapter_run_rejects_unknown_semantic_profile() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        semantic_profile="nope",
    )
    with pytest.raises(IsolatedRunError, match="unknown semantic profile"):
        BacktestEngineAdapter().run(type("Strategy", (), {}), [], config)


def test_adapter_run_isolated_forwards_confirmed_htf_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run

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

    def _capture(source, *, bars, config, resume_state=None, htf_bars=None):
        seen["htf_bars"] = htf_bars
        raise IsolatedRunError("stop")

    monkeypatch.setattr(isolated_run, "run_isolated_artifact", _capture)
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        semantic_profile="strict_5x",
    )
    with pytest.raises(IsolatedRunError, match="stop"):
        BacktestEngineAdapter().run_isolated(
            b"VALUE = 1\n",
            [],
            config,
            htf_bars=htf_bars,
        )
    assert seen["htf_bars"] == htf_bars


def test_adapter_run_isolated_rejects_unconfirmed_htf_bars() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        semantic_profile="strict_5x",
    )
    with pytest.raises(IsolatedRunError, match="confirmed HTF"):
        BacktestEngineAdapter().run_isolated(
            b"VALUE = 1\n",
            [],
            config,
            htf_bars=[
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1D",
                    "time": 0,
                    "open": 40,
                    "high": 43,
                    "low": 39,
                    "close": 42,
                    "volume": 1,
                }
            ],
        )


def test_adapter_run_isolated_forwards_bulk_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run

    seen: dict[str, object] = {}

    def _capture(source, *, bars, config, resume_state=None, htf_bars=None, params=None):
        seen["isolated_protocol"] = getattr(config, "isolated_protocol", None)
        raise IsolatedRunError("stop")

    monkeypatch.setattr(isolated_run, "run_isolated_artifact", _capture)
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        semantic_profile="strict_5x",
    )
    object.__setattr__(config, "isolated_protocol", "bulk_backtest")
    with pytest.raises(IsolatedRunError, match="stop"):
        BacktestEngineAdapter().run_isolated(b"VALUE = 1\\n", [], config)
    assert seen["isolated_protocol"] == "bulk_backtest"
