from __future__ import annotations

from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError

from openpine.cli.runtime_helpers import (
    _build_strategy_backtest_config,
    _build_strategy_replay_config,
    _run_indicator_plot_runtime,
)
from openpine.runtime.engine import BacktestRunConfig


def test_cli_backtest_config_uses_strategy_semantic_profile() -> None:
    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        semantic_profile="strict_5x",
    )
    config = _build_strategy_backtest_config(
        strategy=strategy,
        decl_args={},
        start_ms=0,
        end_ms=60_000,
        capture_plots=False,
        capture_from_ms=None,
        capture_to_ms=None,
        config_cls=BacktestRunConfig,
    )
    assert config.semantic_profile == "strict_5x"


def test_cli_replay_config_uses_strategy_semantic_profile() -> None:
    strategy = SimpleNamespace(
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        semantic_profile="strict_5x",
    )
    config = _build_strategy_replay_config(
        strategy=strategy,
        decl_args={},
        start_ms=0,
        end_ms=60_000,
        config_cls=BacktestRunConfig,
    )
    assert config.semantic_profile == "strict_5x"


def test_cli_isolated_indicator_requires_semantic_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(source, bars, *, semantic_profile=None):
        captured["semantic_profile"] = semantic_profile
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    kwargs = dict(
        generated_class=b"VALUE = 1\n",
        bars=[],
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        provider=SimpleNamespace(),
        compare_from_ms=None,
        compare_to_ms=None,
        progress_every=1,
        console=SimpleNamespace(),
        perf_counter=lambda: 0.0,
    )
    with pytest.raises(TypeError):
        _run_indicator_plot_runtime(**kwargs)
    assert "semantic_profile" not in captured


def test_cli_isolated_indicator_forwards_admitted_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(source, bars, *, semantic_profile=None):
        captured["semantic_profile"] = semantic_profile
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    _run_indicator_plot_runtime(
        generated_class=b"VALUE = 1\n",
        bars=[],
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        provider=SimpleNamespace(),
        compare_from_ms=None,
        compare_to_ms=None,
        progress_every=1,
        console=SimpleNamespace(),
        perf_counter=lambda: 0.0,
        semantic_profile="strict_5x",
    )
    assert captured["semantic_profile"] == "strict_5x"


def test_cli_isolated_indicator_rejects_unknown_profile() -> None:
    with pytest.raises(AdmitError, match="unknown semantic profile"):
        _run_indicator_plot_runtime(
            generated_class=b"VALUE = 1\n",
            bars=[],
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            provider=SimpleNamespace(),
            compare_from_ms=None,
            compare_to_ms=None,
            progress_every=1,
            console=SimpleNamespace(),
            perf_counter=lambda: 0.0,
            semantic_profile="nope",
        )
