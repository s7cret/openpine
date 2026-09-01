from __future__ import annotations

import sys
from pathlib import Path

import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar
from openpine_contracts import Finality

from tests.rc4_fixtures import (
    HASH_A,
    admitted_manifest,
    canonical_bar_envelopes,
    execution_context,
)

SOURCE = (
    "from pinelib.strategy.context import StrategyContext\n"
    "class GeneratedStrategy:\n"
    "    def __init__(self, params=None, runtime=None):\n"
    "        self.ctx = StrategyContext(intent_run_id='run', intent_strategy_id='s')\n"
    "        self.ctx.attach_runtime(runtime)\n"
    "    def _process_bar(self, bar, bar_index):\n"
    "        if bar_index == 2:\n"
    "            self.ctx.entry('L', 'long', qty=1)\n"
)


def _bars() -> list[Bar]:
    return [
        Bar(
            time=1_000 + i,
            open=10.0 + i,
            high=11.0 + i,
            low=9.0 + i,
            close=10.5 + i,
            finality=Finality.FINAL,
        )
        for i in range(6)
    ]


def _cfg(*, semantic_profile: str = "strict_5x") -> BacktestConfig:
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
    )
    cfg.semantic_profile = semantic_profile
    cfg.execution_context = execution_context()  # type: ignore[attr-defined]
    cfg.instrument_id = "test:S"  # type: ignore[attr-defined]
    cfg.admitted_manifest = admitted_manifest()  # type: ignore[attr-defined]
    return cfg














































def _resume_cfg() -> BacktestConfig:
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
        export_resume_state=True,
        resume_validation_policy="diagnostic",
    )
    cfg.semantic_profile = "strict_5x"
    cfg.execution_context = execution_context()  # type: ignore[attr-defined]
    cfg.instrument_id = "test:S"  # type: ignore[attr-defined]
    cfg.admitted_manifest = admitted_manifest()  # type: ignore[attr-defined]
    return cfg
