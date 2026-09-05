"""All adapter paths use the same effective configuration, not legacy aliases."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig


@pytest.mark.parametrize("mode", ["floor", "ceil", "nearest", "none", "truncate", None])
def test_adapter_does_not_reinterpret_engine_rounding(mode):
    config = BacktestRunConfig("SOLUSDT", "1m", 0, 60_000, semantic_profile="strict_5x",
                               qty_rounding_mode=mode, margin_long=0, margin_short=0)
    effective = BacktestEngineAdapter()._to_engine_config(config)
    assert effective.qty_rounding == ("floor" if mode is None else mode)
    assert effective.margin_long == effective.margin_short == 0


def test_trusted_strategy_and_isolated_adapter_share_config_resolution(monkeypatch):
    adapter = BacktestEngineAdapter()
    config = BacktestRunConfig("SOLUSDT", "1m", 0, 60_000, semantic_profile="strict_5x")
    expected = adapter._to_engine_config(config)
    convert = Mock(return_value=expected)
    monkeypatch.setattr(adapter, "_to_engine_config", convert)
    instance = SimpleNamespace(run=Mock(return_value=SimpleNamespace(status="completed")))
    engine = Mock(return_value=instance)
    monkeypatch.setattr(adapter, "_module", SimpleNamespace(BacktestEngine=engine))
    class Strategy: pass
    adapter.run(Strategy, [], config)
    convert.assert_called_once_with(config)
    engine.assert_called_once_with(expected)
