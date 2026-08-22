from __future__ import annotations

from pathlib import Path

import pytest

from openpine.runtime.engine import (
    BacktestArtifactError,
    _load_generated_module,
    load_generated_class_from_artifact,
    load_strategy_class_from_artifact,
)

PKG = Path(__file__).resolve().parents[1] / "openpine"


def test_production_loaders_reject_in_process_flag() -> None:
    with pytest.raises(BacktestArtifactError, match="in-process"):
        load_generated_class_from_artifact("src", "art", unsafe_in_process=True)
    with pytest.raises(BacktestArtifactError, match="in-process"):
        load_strategy_class_from_artifact(
            "src", "art", symbol="BTCUSDT", timeframe="1m", unsafe_in_process=True
        )


def test_private_loader_rejects_in_process_flag(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(BacktestArtifactError, match="in-process"):
        _load_generated_module(path, "src", "art", unsafe_in_process=True)


def test_production_source_never_enables_in_process_import() -> None:
    offenders: list[str] = []
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "unsafe_in_process=True" in text:
            offenders.append(str(path.relative_to(PKG)))
    assert offenders == []


_LEFTOVER_CLASS_LOADERS = (
    "gateway/live_runner.py",
    "gateway/routes/tv_parity.py",
    "gateway/routes/strategies.py",
    "workers/strategy_job_executor.py",
)

_STRATEGY_CLASS_LOADERS = (
    "batch/runner.py",
    "cli/main.py",
    "cli/runtime_helpers.py",
)


def test_leftover_exec_paths_do_not_call_class_loader() -> None:
    offenders: list[str] = []
    for rel in _LEFTOVER_CLASS_LOADERS:
        text = (PKG / rel).read_text(encoding="utf-8")
        if "load_strategy_class_from_artifact" in text or "load_generated_class_from_artifact" in text:
            offenders.append(rel)
    assert offenders == []


def test_strategy_cli_and_batch_do_not_call_class_loader() -> None:
    offenders: list[str] = []
    for rel in _STRATEGY_CLASS_LOADERS:
        text = (PKG / rel).read_text(encoding="utf-8")
        if "load_strategy_class_from_artifact" in text:
            offenders.append(rel)
    assert offenders == []


def test_indicator_cli_and_batch_do_not_call_generated_class_loader() -> None:
    offenders: list[str] = []
    for rel in _STRATEGY_CLASS_LOADERS:
        text = (PKG / rel).read_text(encoding="utf-8")
        if "load_generated_class_from_artifact" in text:
            offenders.append(rel)
    assert offenders == []


def test_isolated_job_and_engine_forward_resume_state() -> None:
    job = (PKG / "workers/strategy_job_executor.py").read_text(encoding="utf-8")
    engine = (PKG / "runtime/engine.py").read_text(encoding="utf-8")
    assert "resume_state=resume_state" in job.split("run_isolated", 1)[1]
    assert "resume_state=resume_state" in engine.split("def run_isolated", 1)[1]
