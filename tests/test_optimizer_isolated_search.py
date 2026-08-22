from __future__ import annotations

import sys

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.optimizer import LocalOptimizerAdapter, OptimizerRunConfig
from openpine.optimizer.isolated_runner import IsolatedOptimizerRunner
from openpine.run_identity import execution_data_snapshot_hash
from openpine.runtime.engine import BacktestRunConfig


SOURCE = b"""
from pinelib.strategy.context import StrategyContext

class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.qty = (params or {})["qty"]
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")

    def _process_bar(self, bar, bar_index=None):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=self.qty)
        if bar_index == 2:
            self.ctx.close("L")
"""


def _bars() -> tuple[Bar, ...]:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="S")
    timeframe = parse_timeframe("1m")
    return tuple(
        Bar(
            instrument=instrument,
            timeframe=timeframe,
            time=1_000 + index * 60_000,
            time_close=1_000 + (index + 1) * 60_000 - 1,
            open=10 + index * 5,
            high=11 + index * 5,
            low=9 + index * 5,
            close=10 + index * 5,
            volume=1,
            closed=True,
        )
        for index in range(4)
    )


def _config() -> BacktestRunConfig:
    return BacktestRunConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=181_000,
        initial_capital=10_000,
        commission_type="none",
        semantic_profile="strict_5x",
    )


def test_external_optimizer_selects_champion_through_isolated_worker(tmp_path) -> None:
    bars = _bars()
    config = _config()
    runner = IsolatedOptimizerRunner(
        source=SOURCE,
        bars=bars,
        config=config,
        expected_data_snapshot_hash=execution_data_snapshot_hash(
            bars=bars,
            exchange=config.exchange,
            market=config.market_type,
            symbol=config.symbol,
            timeframe=config.timeframe,
            start_ms=config.start_time,
            end_ms=config.end_time,
            finality_policy="CLOSED_BAR_ONLY",
        ),
        base_params={},
    )
    adapter = LocalOptimizerAdapter()

    ref = adapter.start_optimization(
        OptimizerRunConfig(
            optimization_id="opt-fixed",
            strategy_id="strategy-1",
            trials=3,
            artifact_id="artifact-1",
            params_hash="base-hash",
            data_query={"symbol": "S", "timeframe": "1m", "from": 1_000, "to": 181_000},
            parameters=(
                {
                    "name": "qty",
                    "type": "int",
                    "default": 1,
                    "min": 1,
                    "max": 3,
                    "step": 1,
                },
            ),
            runner=runner,
            objective="net_profit",
            output_dir=tmp_path,
            storage_backend="json",
        )
    )
    result = adapter.get_result(ref.optimization_id)

    assert ref.optimization_id == "opt-fixed"
    assert result.status == "completed"
    assert result.trials_requested == 3
    assert result.trials_completed == 3
    assert result.best_params == {"qty": 3}
    assert result.metrics["net_profit"] == 30.0
    assert result.uses_backtest_engine_path is True
    assert result.metrics["runner_adapter"] == "IsolatedOptimizerRunner"
    assert all(item["result_content_hash"] for item in result.trial_metadata)
    assert not any(name.startswith("openpine_generated_") for name in sys.modules)
