from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from openpine.cli import ops
from openpine.cli.ops import jobs, queue, service
from openpine.jobs import Job, JobStatus, JobType


def _reset_scheduler():
    ops._cli_scheduler._queue.clear()
    ops._cli_scheduler._jobs.clear()
    ops._cli_scheduler._running.clear()
    ops._cli_scheduler._locks.clear()
    ops._cli_scheduler._idempotency_map.clear()


def test_jobs_list_show_cancel_retry_and_queue_status():
    _reset_scheduler()
    failed = ops._cli_scheduler.enqueue(
        Job(
            job_type=JobType.REPORT,
            strategy_id="s1",
            status=JobStatus.FAILED,
            error="x",
        )
    )
    pending = ops._cli_scheduler.enqueue(
        Job(job_type=JobType.BACKTEST, strategy_id="s2")
    )
    runner = CliRunner()

    listed = runner.invoke(jobs, ["list"])
    assert listed.exit_code == 0
    assert "type=report" in listed.output
    assert "type=backtest" in listed.output

    shown = runner.invoke(jobs, ["show", failed.id])
    assert shown.exit_code == 0
    assert "Job:" in shown.output
    assert "error" in shown.output

    retry = runner.invoke(jobs, ["retry", failed.id])
    assert retry.exit_code == 0
    assert ops._cli_scheduler.get_job(failed.id).status == JobStatus.PENDING

    cancel = runner.invoke(jobs, ["cancel", pending.id])
    assert cancel.exit_code == 0
    assert ops._cli_scheduler.get_job(pending.id).status == JobStatus.CANCELLED

    status = runner.invoke(queue, ["status"])
    assert status.exit_code == 0
    assert "Total jobs" in status.output


def test_jobs_missing_and_retry_non_failed():
    _reset_scheduler()
    job = ops._cli_scheduler.enqueue(Job(job_type=JobType.BACKTEST, strategy_id="s1"))
    runner = CliRunner()

    assert runner.invoke(jobs, ["show", "missing"]).exit_code == 1
    assert runner.invoke(jobs, ["cancel", "missing"]).exit_code == 1
    assert runner.invoke(jobs, ["retry", "missing"]).exit_code == 1
    retry = runner.invoke(jobs, ["retry", job.id])
    assert retry.exit_code == 1
    assert "not failed" in retry.output


def test_enqueue_live_bar_dry_run_and_real_enqueue():
    _reset_scheduler()
    runner = CliRunner()
    dry = runner.invoke(
        jobs,
        ["enqueue-live-bar", "--strategy", "s1", "--bar-time", "123", "--dry-run"],
    )
    assert dry.exit_code == 0
    assert "Would enqueue" in dry.output
    assert ops._cli_scheduler.list_jobs() == []

    created = runner.invoke(
        jobs,
        [
            "enqueue-live-bar",
            "--strategy",
            "s1",
            "--bar-time",
            "123",
            "--status",
            "paper",
        ],
    )
    assert created.exit_code == 0
    job = ops._cli_scheduler.list_jobs()[0]
    assert job.job_type == JobType.LIVE_BAR_PROCESS
    assert job.input["bar_time"] == 123
    assert job.input["status_override"] == "paper"


def test_queue_status_empty():
    _reset_scheduler()
    result = CliRunner().invoke(queue, ["status"])
    assert result.exit_code == 0
    assert "no jobs" in result.output


def test_service_commands_fail_cleanly_when_systemd_unavailable(monkeypatch):
    monkeypatch.setattr("openpine.cli.ops._systemd_available", lambda: False)
    runner = CliRunner()
    for command in ["install", "start", "stop", "restart", "logs", "enable", "disable"]:
        result = runner.invoke(service, [command])
        assert result.exit_code == 1
        assert "systemd is not available" in result.output


def test_service_install_writes_env_aware_user_unit(monkeypatch, tmp_path):
    monkeypatch.setattr("openpine.cli.ops._systemd_available", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/opt/openpine/.venv/bin/openpine")

    result = CliRunner().invoke(service, ["install"])

    assert result.exit_code == 0
    unit = tmp_path / ".config" / "systemd" / "user" / "openpine.service"
    text = unit.read_text()
    assert "WorkingDirectory=%h/.openpine" in text
    assert "Environment=OPENPINE_HOME=%h/.openpine" in text
    assert "EnvironmentFile=-%h/.config/openpine/openpine.env" in text
    assert "ExecStart=/opt/openpine/.venv/bin/openpine daemon run" in text
    assert "OPENPINE_ALLOW_PICKLE_STATE=1" not in text
