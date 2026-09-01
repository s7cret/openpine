from __future__ import annotations

from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[1] / "openpine"






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








def test_isolated_job_and_engine_forward_resume_state() -> None:
    job = (PKG / "workers/strategy_job_executor.py").read_text(encoding="utf-8")
    engine = (PKG / "runtime/engine.py").read_text(encoding="utf-8")
    assert "resume_state=resume_state" in job.split("run_isolated", 1)[1]
    assert "resume_state=resume_state" in engine.split("def run_isolated", 1)[1]
