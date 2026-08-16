from __future__ import annotations

from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError

from openpine.batch.runner import _build_strategy_run_config
from openpine.runtime.engine import BacktestRunConfig


def _args(**overrides) -> SimpleNamespace:
    values = dict(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        qty_step=0.001,
        qty_rounding_mode="down",
        semantic_profile=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _build(**overrides):
    chart = SimpleNamespace(start_ms=0, end_ms=60_000, timeframe="1m")
    data_meta = {"calculation_from": 0, "calculation_to": 60_000}
    kwargs = dict(
        chart=chart,
        args=_args(),
        data_meta=data_meta,
        decl_args={},
        config_cls=BacktestRunConfig,
    )
    kwargs.update(overrides)
    return _build_strategy_run_config(**kwargs)


def test_batch_strategy_config_requires_semantic_profile() -> None:
    with pytest.raises(AdmitError, match="semantic profile"):
        _build()


def test_batch_strategy_config_forwards_admitted_profile() -> None:
    config = _build(args=_args(semantic_profile="legacy_4x"))
    assert config.semantic_profile == "legacy_4x"


def test_batch_strategy_config_rejects_unknown_profile() -> None:
    with pytest.raises(AdmitError, match="unknown semantic profile"):
        _build(args=_args(semantic_profile="nope"))
