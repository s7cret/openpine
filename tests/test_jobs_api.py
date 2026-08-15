import importlib.util
from pathlib import Path

from fastapi import HTTPException

from openpine.jobs.models import Job, JobStatus, JobType


def _jobs_mod():
    path = Path(__file__).resolve().parents[1] / "openpine" / "gateway" / "routes" / "jobs.py"
    spec = importlib.util.spec_from_file_location("openpine_jobs_route", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scheduler_job_requires_id() -> None:
    job = Job(job_type=JobType.BACKTEST, id="")
    try:
        _jobs_mod()._from_scheduler_job(job)
    except HTTPException as exc:
        assert exc.status_code == 500
    else:
        raise AssertionError("missing job id must fail")


def test_scheduler_job_keeps_stable_id() -> None:
    job = Job(job_type=JobType.BACKTEST, id="run-1", status=JobStatus.DONE)
    payload = _jobs_mod()._from_scheduler_job(job)
    assert payload["job_id"] == "run-1"
    assert payload["href"] == "/backtests/run-1"
    assert payload["kind"] == "backtest"
