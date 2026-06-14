from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from openpine.runtime import engine as rt


def test_runtime_artifact_loading_edges(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "art"
    artifact_dir.mkdir()
    (artifact_dir / "generated_strategy.py").write_text(
        "class CustomStrategy:\n"
        "    def _process_bar(self, bar):\n"
        "        return None\n"
        "class ImportedBase:\n"
        "    pass\n",
        encoding="utf-8",
    )
    artifact = {
        "artifact_dir": str(artifact_dir),
        "compile_meta": {"compile_status": "OK", "class_name": "CustomStrategy"},
    }

    class Store:
        def get_artifact(self, artifact_id, source_id):
            if artifact_id == "missing-artifact":
                raise FileNotFoundError(artifact_id)
            if artifact_id == "bad-status":
                return {"artifact_dir": str(artifact_dir), "compile_meta": {"compile_status": "FAIL"}}
            if artifact_id == "unsafe":
                return {"artifact_dir": str(artifact_dir), "compile_meta": {"compile_status": "OK", "unsafe": True, "unsafe_reasons": ["x"]}}
            if artifact_id == "missing-file":
                return {"artifact_dir": str(tmp_path / "none"), "compile_meta": {"compile_status": "OK"}}
            return artifact

    import openpine.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ArtifactStore", Store)
    cls = rt.load_strategy_class_from_artifact("src", "ok", symbol="BTCUSDT", timeframe="1m")
    assert cls.__name__ == "CustomStrategy"
    assert rt.load_generated_class_from_artifact("src", "ok").__name__ == "CustomStrategy"
    for artifact_id in ["missing-artifact", "bad-status", "unsafe", "missing-file"]:
        with pytest.raises(rt.BacktestArtifactError):
            rt.load_generated_class_from_artifact("src", artifact_id)

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "generated_strategy.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(rt.BacktestArtifactError):
        rt._select_strategy_class(rt._load_generated_module(bad_dir / "generated_strategy.py", "s", "a"), {})


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
    config = rt.BacktestRunConfig(symbol="BTCUSDT", timeframe="1m", start_time=1, end_time=2, capture_plots=True)
    class Strategy:
        pass
    result = adapter.run(Strategy, [SimpleNamespace(time=1), SimpleNamespace(time=2)], config, params={"p": 1}, progress_callback=lambda d, t: progress.append((d, t)), runtime_data_provider=object(), effective_pre_bars=1)
    assert result.status == "ok"
    assert result.bars_processed == 2
    assert result.resume_state == {"r": 1}
    assert progress
    assert calls[0][1]["runtime_kwargs"]["symbol"] == "BTCUSDT"
