"""Operational CLI command groups."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console

from openpine.jobs import Job, JobScheduler, JobStatus, JobType

console = Console()
_cli_scheduler = JobScheduler()


def _fmt_utc_ms(timestamp_ms: int) -> str:
    return f"{datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc):%Y-%m-%d %H:%M:%S}"


def _fmt_utc_ms_as(timestamp_ms: int, fmt: str) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).strftime(fmt)


@click.group()
def jobs() -> None:
    """Job management commands."""
    pass


@jobs.command("list")
def jobs_list() -> None:
    """List all jobs (scheduled, running, done)."""
    jobs = _cli_scheduler.list_jobs()
    if not jobs:
        console.print("[dim]No jobs[/dim]")
        return
    for j in jobs:
        console.print(
            f"  [{j.status.value}] {j.id[:8]}  type={j.type.value}  "
            f"strategy={getattr(j, 'strategy_id', '-')}  "
            f"created={_fmt_utc_ms_as(j.created_at_ms, '%H:%M:%S')}"
        )


@jobs.command("show")
@click.argument("job_id")
def jobs_show(job_id: str) -> None:
    """Show detailed information about a specific job."""
    job = _cli_scheduler.get_job(job_id)
    if job is None:
        console.print(f"[red]Job not found: {job_id}[/red]")
        sys.exit(1)
    console.print(f"[bold]Job: {job.id}[/bold]")
    console.print(f"  type:          {job.job_type.value if hasattr(job.job_type, 'value') else job.job_type}")
    console.print(f"  status:        {job.status.value if hasattr(job.status, 'value') else job.status}")
    console.print(f"  strategy_id:   {job.strategy_id or '-'}")
    console.print(f"  priority:      {job.priority}")
    console.print(f"  idempotency_key: {job.idempotency_key or '-'}")
    console.print(f"  created_at:    {_fmt_utc_ms(job.created_at)} UTC")
    if job.started_at:
        console.print(f"  started_at:    {_fmt_utc_ms(job.started_at)} UTC")
    if job.finished_at:
        console.print(f"  finished_at:   {_fmt_utc_ms(job.finished_at)} UTC")
    if job.error:
        console.print(f"  error:         {job.error}")
    if job.result:
        console.print(f"  result:        {job.result}")


@jobs.command("cancel")
@click.argument("job_id")
def jobs_cancel(job_id: str) -> None:
    """Cancel a pending or running job."""
    job = _cli_scheduler.get_job(job_id)
    if job is None:
        console.print(f"[red]Job not found: {job_id}[/red]")
        sys.exit(1)
    current_status = job.status.value if hasattr(job.status, 'value') else job.status
    _cli_scheduler.cancel(job_id)
    console.print(f"[green]Job {job_id} cancelled (was {current_status}).[/green]")


@jobs.command("retry")
@click.argument("job_id")
def jobs_retry(job_id: str) -> None:
    """Retry a failed job by re-enqueuing it."""
    from openpine.jobs.models import JobStatus

    job = _cli_scheduler.get_job(job_id)
    if job is None:
        console.print(f"[red]Job not found: {job_id}[/red]")
        sys.exit(1)
    if job.status != JobStatus.FAILED:
        console.print(f"[yellow]Job {job_id} is not failed (status={job.status}). Cannot retry.[/yellow]")
        sys.exit(1)
    # Reset job to pending and enqueue again
    job.status = JobStatus.PENDING
    job.error = None
    job.finished_at = None
    job.started_at = None
    job.attempt = 1
    _cli_scheduler.enqueue(job)
    console.print(f"[green]Job {job_id} re-enqueued for retry.[/green]")


@jobs.command("enqueue-live-bar")
@click.option("--status", type=str, default=None, help="Override status")
@click.option("--strategy", required=True, help="Strategy ID")
@click.option("--bar-time", required=True, type=int, help="Bar timestamp in ms")
@click.option("--dry-run", is_flag=True, help="Show what would be enqueued without creating")
def jobs_enqueue_live_bar(
    status: str | None,
    strategy: str,
    bar_time: int,
    dry_run: bool,
) -> None:
    """Enqueue a live-bar ingestion job (dry-run shows the job that would be created)."""
    from openpine.jobs import Job, JobStatus, JobType

    job = Job(
        type=JobType.LIVE_BAR_INGESTION,
        strategy_id=strategy,
        params_hash="dry_run",
        instrument_key="BTCUSDT",
        timeframe="15m",
        bar_time=bar_time,
        status=JobStatus.SCHEDULED,
    )

    if dry_run:
        console.print(f"[dim]Would enqueue job:[/dim]")
        console.print(f"  type:        {job.type.value}")
        console.print(f"  strategy:    {job.strategy_id}")
        console.print(f"  bar_time:    {bar_time}")
        console.print("  → would be enqueued (no existing job with this idempotency_key)")
        return
    result = _cli_scheduler.enqueue(job)
    console.print(f"[green]Enqueued job {result.id[:8]}[/green]")


@click.group()
def service() -> None:
    """Systemd service management commands."""
    pass


def _systemd_available() -> bool:
    """Check if systemd is available."""
    import os
    import subprocess
    if os.name != "posix":
        return False
    try:
        subprocess.run(["systemctl", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@service.command("install")
def service_install() -> None:
    """Install OpenPine as a systemd user service."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        console.print("[dim]Service management requires Linux with systemd.[/dim]")
        sys.exit(1)

    service_file = Path.home() / ".config" / "systemd" / "user" / "openpine.service"
    service_file.parent.mkdir(parents=True, exist_ok=True)

    # Detect openpine binary path
    import shutil as _shutil
    openpine_bin = _shutil.which("openpine") or "openpine"

    service_content = f"""[Unit]
Description=OpenPine Trading Platform
After=network.target

[Service]
Type=simple
ExecStart={openpine_bin} daemon run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
"""

    service_file.write_text(service_content)
    console.print(f"[green]Service file written to {service_file}[/green]")
    console.print("")
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. Reload systemd:    systemctl --user daemon-reload")
    console.print(f"  2. Enable service:    openpine service enable")
    console.print(f"  3. Start service:    openpine service start")


@service.command("start")
def service_start() -> None:
    """Start the OpenPine service."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    try:
        subprocess.run(["systemctl", "--user", "start", "openpine"], check=True)
        console.print("[green]OpenPine service started.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to start service: {e}[/red]")
        sys.exit(1)


@service.command("stop")
def service_stop() -> None:
    """Stop the OpenPine service."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    try:
        subprocess.run(["systemctl", "--user", "stop", "openpine"], check=True)
        console.print("[green]OpenPine service stopped.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to stop service: {e}[/red]")
        sys.exit(1)


@service.command("restart")
def service_restart() -> None:
    """Restart the OpenPine service."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    try:
        subprocess.run(["systemctl", "--user", "restart", "openpine"], check=True)
        console.print("[green]OpenPine service restarted.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to restart service: {e}[/red]")
        sys.exit(1)


@service.command("status")
def service_status() -> None:
    """Check the OpenPine service status."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    result = subprocess.run(
        ["systemctl", "--user", "status", "openpine"],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)
    if result.returncode != 0:
        console.print("[yellow]Service is not running.[/yellow]")


@service.command("logs")
@click.option("--lines", "-n", default=50, help="Number of log lines to show")
def service_logs(lines: int) -> None:
    """Show recent OpenPine service logs."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "openpine", "-n", str(lines)],
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to fetch logs: {e}[/red]")
        sys.exit(1)


@service.command("enable")
def service_enable() -> None:
    """Enable OpenPine service for auto-start."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    try:
        subprocess.run(["systemctl", "--user", "enable", "openpine"], check=True)
        console.print("[green]OpenPine service enabled for auto-start.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to enable service: {e}[/red]")
        sys.exit(1)


@service.command("disable")
def service_disable() -> None:
    """Disable OpenPine service auto-start."""
    if not _systemd_available():
        console.print("[red]systemd is not available on this system.[/red]")
        sys.exit(1)
    import subprocess
    try:
        subprocess.run(["systemctl", "--user", "disable", "openpine"], check=True)
        console.print("[green]OpenPine service disabled.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to disable service: {e}[/red]")
        sys.exit(1)


@click.group()
def queue() -> None:
    """Job queue status commands."""
    pass


@queue.command("status")
def queue_status() -> None:
    """Show job queue depth and status breakdown."""
    from openpine.jobs.models import JobStatus

    scheduler = _cli_scheduler
    console.print("[bold]Job Queue Status[/bold]")
    all_jobs = scheduler.list_jobs()
    if not all_jobs:
        console.print("[dim](no jobs in queue)[/dim]")
        return

    counts: dict[str, int] = {}
    for status in JobStatus:
        counts[status.value] = 0
    for j in all_jobs:
        counts[j.status.value] = counts.get(j.status.value, 0) + 1

    total = len(all_jobs)
    console.print(f"Total jobs: {total}")
    for status, count in counts.items():
        if count > 0:
            color = {
                "pending": "yellow",
                "running": "green",
                "done": "dim",
                "failed": "red",
                "cancelled": "dim",
            }.get(status, "dim")
            console.print(f"  [{color}]{status}[/{color}]: {count}")


@click.group()
def workers() -> None:
    """Worker pool management commands."""
    pass


@workers.command("status")
def workers_status() -> None:
    """Show worker pool status."""
    from openpine.jobs import Job, JobScheduler, JobStatus, JobType
    from openpine.workers import AggregationWorkerPool, FeatureWorkerPool

    scheduler = JobScheduler()
    console.print("[bold]Worker Pool Status[/bold]")

    pools = [
        ("AggregationWorkerPool", AggregationWorkerPool(scheduler)),
        ("FeatureWorkerPool", FeatureWorkerPool(scheduler)),
    ]

    for name, pool in pools:
        status = pool.get_status()
        console.print(f"\n  [bold]{name}[/bold]")
        console.print(f"    Running:     {status.get('running', False)}")
        console.print(f"    Max workers: {status.get('max_workers', 0)}")
        console.print(f"    Active:      {status.get('active_workers', 0)}")
        heartbeats = status.get("heartbeats", {})
        if heartbeats:
            console.print(f"    Heartbeats:  {len(heartbeats)}")
        else:
            console.print(f"    Heartbeats:  0")


@workers.command("pause")
def workers_pause() -> None:
    """Pause all worker pools."""
    from openpine.jobs import Job, JobScheduler, JobStatus, JobType
    from openpine.workers import AggregationWorkerPool, FeatureWorkerPool

    scheduler = JobScheduler()
    pools = [
        AggregationWorkerPool(scheduler),
        FeatureWorkerPool(scheduler),
    ]
    for pool in pools:
        pool.stop()
    console.print("[green]All worker pools paused.[/green]")


@workers.command("resume")
def workers_resume() -> None:
    """Resume all worker pools."""
    from openpine.jobs import Job, JobScheduler, JobStatus, JobType
    from openpine.workers import AggregationWorkerPool, FeatureWorkerPool

    scheduler = JobScheduler()
    pools = [
        AggregationWorkerPool(scheduler),
        FeatureWorkerPool(scheduler),
    ]
    for pool in pools:
        pool.start()
    console.print("[green]All worker pools resumed.[/green]")
