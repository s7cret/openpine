"""Operational CLI command groups."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from openpine.jobs import JobScheduler

console = Console()
_cli_scheduler = JobScheduler()


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
    from openpine.jobs import JobScheduler
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
    from openpine.jobs import JobScheduler
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
    from openpine.jobs import JobScheduler
    from openpine.workers import AggregationWorkerPool, FeatureWorkerPool

    scheduler = JobScheduler()
    pools = [
        AggregationWorkerPool(scheduler),
        FeatureWorkerPool(scheduler),
    ]
    for pool in pools:
        pool.start()
    console.print("[green]All worker pools resumed.[/green]")
