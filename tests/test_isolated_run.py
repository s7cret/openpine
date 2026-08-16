from __future__ import annotations

import sys

import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar

from openpine.runtime.isolated_run import (
    IsolatedRunError,
    capture_generated_source,
    run_isolated_artifact,
    run_isolated_from_store,
)

SOURCE = (
    "from pinelib.strategy.context import StrategyContext\n"
    "ctx = StrategyContext(intent_run_id='run', intent_strategy_id='s')\n"
    "ctx._runtime = type('RT', (), {"
    "'bar_index': 2, "
    "'current_bar': type('B', (), {'time': 1002})()"
    "})()\n"
    "ctx.entry('L', 'long', qty=1)\n"
)


def _bars() -> list[Bar]:
    return [
        Bar(time=1_000 + i, open=10.0 + i, high=11.0 + i, low=9.0 + i, close=10.5 + i)
        for i in range(6)
    ]


def _cfg(*, semantic_profile: str = "legacy_4x") -> BacktestConfig:
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
    return cfg


def test_isolated_run_replays_live_tape_without_importing_generated() -> None:
    result = run_isolated_artifact(SOURCE.encode("utf-8"), bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["schema_id"] == "openpine.intent.v2"
    assert result["intent_tape"][0]["kind"] == "entry"
    assert result["score_ledger_hash"]
    assert not any(name.startswith("openpine_generated_") for name in sys.modules)

    class LiveEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 2:
                self.ctx.entry("L", "long", qty=1.0)

    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    assert result["score_ledger_hash"] == live.score_ledger_hash


def test_isolated_run_rejects_artifact_without_tape() -> None:
    with pytest.raises(IsolatedRunError, match="live pinelib tape"):
        run_isolated_artifact(b"VALUE = 1\n", bars=_bars(), config=_cfg())


def test_isolated_run_drives_generated_class_to_same_hash() -> None:
    source = (
        "from pinelib.strategy.context import StrategyContext\n"
        "class GeneratedStrategy:\n"
        "    def __init__(self, params=None, runtime=None):\n"
        "        self.rt = runtime\n"
        "        self.ctx = StrategyContext(intent_run_id='run', intent_strategy_id='s')\n"
        "    def _process_bar(self, bar, bar_index=None):\n"
        "        idx = self.rt.bar_index if bar_index is None else bar_index\n"
        "        if idx != 2:\n"
        "            return\n"
        "        self.ctx._runtime = type('RT', (), {"
        "'bar_index': 2, "
        "'current_bar': type('B', (), {'time': getattr(bar, 'time', 1002)})()"
        "})()\n"
        "        self.ctx.entry('L', 'long', qty=1)\n"
    )
    result = run_isolated_artifact(source.encode("utf-8"), bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["bar_index"] == 2

    class LiveEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 2:
                self.ctx.entry("L", "long", qty=1.0)

    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    assert result["score_ledger_hash"] == live.score_ledger_hash


def test_capture_generated_source_uses_bytes_not_later_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    artifact_dir = tmp_path / "art"
    artifact_dir.mkdir()
    path = artifact_dir / "generated_strategy.py"
    path.write_text(SOURCE, encoding="utf-8")

    class Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            return {
                "artifact_dir": str(artifact_dir),
                "compile_meta": {"compile_status": "OK"},
            }

    import openpine.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ArtifactStore", Store)
    captured = capture_generated_source("src", "art")
    path.write_text("VALUE = 999\n", encoding="utf-8")
    assert captured == SOURCE.encode("utf-8")
    result = run_isolated_artifact(captured, bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["qty"] == "1"


def test_run_isolated_from_store_captures_then_replays(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    artifact_dir = tmp_path / "art"
    artifact_dir.mkdir()
    (artifact_dir / "generated_strategy.py").write_text(SOURCE, encoding="utf-8")

    class Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            return {
                "artifact_dir": str(artifact_dir),
                "compile_meta": {"compile_status": "OK"},
            }

    import openpine.artifacts as artifacts

    monkeypatch.setattr(artifacts, "ArtifactStore", Store)
    result = run_isolated_from_store("src", "art", bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["kind"] == "entry"
    assert result["score_ledger_hash"]


def test_isolated_indicator_returns_plot_tuples() -> None:
    from openpine.runtime.isolated_run import run_isolated_indicator

    source = (
        "class GeneratedStrategy:\n"
        "    def __init__(self, params=None, runtime=None):\n"
        "        self.rt = runtime\n"
        "    def _process_bar(self, bar, i=0):\n"
        "        self.rt.plot_recorder.record_plot(int(bar.time), int(i), bar.close, 'close')\n"
    )
    result = run_isolated_indicator(
        source.encode("utf-8"),
        _bars()[:2],
        semantic_profile="strict_5x",
    )
    assert result.plots
    assert result.plots[0][3] == "close"
    assert result.plots[0][0] == 1000
    assert isinstance(result.plots[0][2], str)


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
    cfg.semantic_profile = "legacy_4x"
    return cfg


def test_isolated_run_honors_resume_state_without_double_entry() -> None:
    first = run_isolated_artifact(
        SOURCE.encode("utf-8"),
        bars=_bars(),
        config=_resume_cfg(),
    )
    resume = getattr(first["raw_result"], "resume_state", None)
    assert resume is not None
    second = run_isolated_artifact(
        SOURCE.encode("utf-8"),
        bars=_bars(),
        config=_resume_cfg(),
        resume_state=resume,
    )
    assert second["score_ledger_hash"]
    assert getattr(second["raw_result"], "resume_state", None) is not None


def test_isolated_resume_skips_already_replayed_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run

    applied: list[int] = []
    real = isolated_run.apply_live_intents_for_bar

    def _capture(ctx, tape, bar_index):
        applied.append(int(bar_index))
        return real(ctx, tape, bar_index)

    monkeypatch.setattr(isolated_run, "apply_live_intents_for_bar", _capture)
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_010,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_010,
        export_resume_state=True,
        resume_validation_policy="diagnostic",
    )
    cfg.semantic_profile = "legacy_4x"
    first = run_isolated_artifact(SOURCE.encode("utf-8"), bars=_bars()[:3], config=cfg)
    resume = first["raw_result"].resume_state
    assert resume is not None
    warnings = [getattr(item, "code", "") for item in (first["raw_result"].warnings or [])]
    assert "RESUME_STRATEGY_STATE_UNAVAILABLE" not in warnings
    applied.clear()
    run_isolated_artifact(
        SOURCE.encode("utf-8"),
        bars=_bars(),
        config=cfg,
        resume_state=resume,
    )
    assert applied
    assert min(applied) > int(resume.bar_index)
    assert 0 not in applied


def test_isolated_run_requires_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}

    def _capture(source, **kwargs):
        seen["semantic_profile"] = kwargs.get("semantic_profile")
        raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _capture)
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        run_isolated_artifact(
            SOURCE.encode("utf-8"),
            bars=_bars(),
            config=_cfg(semantic_profile=""),
        )
    assert "semantic_profile" not in seen


def test_isolated_run_forwards_config_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}

    def _capture(source, **kwargs):
        seen["semantic_profile"] = kwargs.get("semantic_profile")
        raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _capture)
    cfg = _cfg()
    cfg.semantic_profile = "strict_5x"
    with pytest.raises(IsolatedRunError, match="stop"):
        run_isolated_artifact(SOURCE.encode("utf-8"), bars=_bars(), config=cfg)
    assert seen["semantic_profile"] == "strict_5x"


def test_isolated_indicator_forwards_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_run import run_isolated_indicator
    from openpine.runtime.isolated_worker import IsolatedWorkerError

    seen: dict[str, object] = {}

    def _capture(source, **kwargs):
        seen["semantic_profile"] = kwargs.get("semantic_profile")
        raise IsolatedWorkerError("stop")

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _capture)
    with pytest.raises(IsolatedRunError, match="stop"):
        run_isolated_indicator(b"VALUE = 1\n", _bars(), semantic_profile="strict_5x")
    assert seen["semantic_profile"] == "strict_5x"


def test_isolated_indicator_requires_semantic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_run as isolated_run
    from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_indicator

    seen: dict[str, object] = {}

    def _ok(source, **kwargs):
        seen["semantic_profile"] = kwargs.get("semantic_profile")
        return {"ok": True, "plots": []}

    monkeypatch.setattr(isolated_run, "evaluate_artifact", _ok)
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        run_isolated_indicator(b"VALUE = 1\n", _bars())
    assert "semantic_profile" not in seen


def test_isolated_indicator_rejects_unknown_profile() -> None:
    from openpine.runtime.isolated_run import run_isolated_indicator

    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        run_isolated_indicator(b"VALUE = 1\n", _bars(), semantic_profile="nope")
