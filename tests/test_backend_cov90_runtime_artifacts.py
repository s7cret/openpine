from __future__ import annotations

import sys
import types
from types import SimpleNamespace
import importlib.util

import pytest

from openpine.runtime import engine as rt




def test_runtime_adapter_run_and_progress(monkeypatch):
    calls = []
    backtest_engine = types.ModuleType("backtest_engine")
    models = types.ModuleType("backtest_engine.models")
    callbacks_mod = types.ModuleType("backtest_engine.models.callbacks")

    class BacktestCallbacks:
        def __init__(self, on_bar_end=None):
            self.on_bar_end = on_bar_end

    callbacks_mod.BacktestCallbacks = BacktestCallbacks

    class EngineConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Result:
        status = "ok"
        resume_state = {"r": 1}

    class BacktestEngine:
        def __init__(self, config):
            self.config = config

        def run(self, strategy_class, **kwargs):
            calls.append((strategy_class, kwargs))
            cb = kwargs.get("callbacks")
            if cb and cb.on_bar_end:
                cb.on_bar_end(object(), 0, object())
                cb.on_bar_end(object(), len(kwargs["bars"]) - 1, object())
            return Result()

    models.BacktestConfig = EngineConfig
    backtest_engine.BacktestConfig = EngineConfig
    backtest_engine.BacktestEngine = BacktestEngine
    backtest_engine.models = models
    monkeypatch.setitem(sys.modules, "backtest_engine", backtest_engine)
    monkeypatch.setitem(sys.modules, "backtest_engine.models", models)
    monkeypatch.setitem(sys.modules, "backtest_engine.models.callbacks", callbacks_mod)
    monkeypatch.setattr(rt, "import_library", lambda name: backtest_engine)
    monkeypatch.setattr(rt.BacktestEngineAdapter, "_to_engine_bar", lambda self, bar: bar)
    adapter = rt.BacktestEngineAdapter()
    progress = []
    config = rt.BacktestRunConfig(symbol="BTCUSDT", timeframe="1m", start_time=1, end_time=2, capture_plots=True, semantic_profile="strict_5x")
    class Strategy:
        pass
    result = adapter.run(Strategy, [SimpleNamespace(time=1), SimpleNamespace(time=2)], config, params={"p": 1}, progress_callback=lambda d, t: progress.append((d, t)), runtime_data_provider=object(), effective_pre_bars=1)
    assert result.status == "ok"
    assert result.bars_processed == 2
    assert result.resume_state == {"r": 1}
    assert progress
    assert calls[0][1]["runtime_kwargs"]["symbol"] == "BTCUSDT"
