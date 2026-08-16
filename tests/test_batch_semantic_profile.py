from __future__ import annotations

from types import SimpleNamespace

from openpine.batch.runner import _build_strategy_run_config
from openpine.runtime.engine import BacktestRunConfig


def test_batch_strategy_config_uses_strict_5x() -> None:
    chart = SimpleNamespace(start_ms=0, end_ms=60_000, timeframe="1m")
    args = SimpleNamespace(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        qty_step=0.001,
        qty_rounding_mode="down",
    )
    data_meta = {"calculation_from": 0, "calculation_to": 60_000}
    config = _build_strategy_run_config(
        chart=chart,
        args=args,
        data_meta=data_meta,
        decl_args={},
        config_cls=BacktestRunConfig,
    )
    assert config.semantic_profile == "strict_5x"
