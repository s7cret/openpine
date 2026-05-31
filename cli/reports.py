"""Report CLI commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()

_KNOWN_REPORTS = {
    "strategy_summary": {
        "id": "strategy_summary",
        "description": "Strategy execution summary",
        "status": "available",
    },
    "data_coverage": {
        "id": "data_coverage",
        "description": "Data coverage report",
        "status": "available",
    },
    "worker_health": {
        "id": "worker_health",
        "description": "Worker pool health report",
        "status": "available",
    },
}


def _report_search_names(report_id: str) -> set[str]:
    return {report_id, report_id.replace("-", "_"), report_id.replace("_", "-")}


def _find_report_files(reports_dir: Path, report_id: str) -> list[Path]:
    """Find report files matching an id, newest first."""
    if not reports_dir.exists():
        return []

    names = _report_search_names(report_id)
    found = [
        path
        for path in reports_dir.rglob("*")
        if path.is_file()
        and (
            path.stem in names
            or path.name in names
            or any(name in path.stem for name in names)
        )
    ]
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def _load_report_file(path: Path) -> object:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text())
    return path.read_text()


@click.group()
def reports() -> None:
    """Report generation commands."""
    pass


@reports.command("list")
def reports_list() -> None:
    """List available reports."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    console.print("[bold]Available Reports[/bold]")
    for report in _KNOWN_REPORTS.values():
        console.print(f"  {report['id']}  - {report['description']}")

    reports_dir = config.data_dir / "reports"
    files = sorted(reports_dir.rglob("*")) if reports_dir.exists() else []
    files = [path for path in files if path.is_file()]
    if files:
        console.print("\n[bold]Report files[/bold]")
        for path in files:
            console.print(f"  {path.relative_to(reports_dir)}")


@reports.command("show")
@click.argument("report_id")
def reports_show(report_id: str) -> None:
    """Show a specific report."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    reports_dir = config.data_dir / "reports"
    found = _find_report_files(reports_dir, report_id)
    if found:
        report_file = found[0]
        content = _load_report_file(report_file)
        console.print(f"[bold]Report:[/bold] {report_file.relative_to(reports_dir)}")
        if isinstance(content, (dict, list)):
            console.print(json.dumps(content, indent=2, default=str))
        else:
            console.print(content)
        return

    report = _KNOWN_REPORTS.get(report_id)
    if report is None:
        console.print(f"[red]Report not found: {report_id}[/red]")
        console.print(f"[dim]Searched: {reports_dir}[/dim]")
        raise SystemExit(1)

    console.print(f"[bold]Report:[/bold] {report['id']}")
    console.print(f"description: {report['description']}")
    console.print(f"status:      {report['status']}")
    console.print(f"reports_dir: {reports_dir}")


@reports.command("export")
@click.argument("report_id")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "csv"]), help="Export format")
def reports_export(report_id: str, fmt: str) -> None:
    """Export a report to JSON or CSV format."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    reports_dir = config.data_dir / "reports"

    console.print(f"[bold]Exporting report:[/bold] {report_id} ({fmt})")

    found = _find_report_files(reports_dir, report_id)

    if found:
        report_file = found[0]
        console.print(f"  Found: {report_file}")
        content = _load_report_file(report_file)
        if fmt == "json":
            if isinstance(content, (dict, list)):
                console.print(json.dumps(content, indent=2, default=str))
            else:
                console.print(json.dumps({"id": report_id, "content": content}, indent=2))
        else:
            if isinstance(content, dict):
                console.print(",".join(content.keys()))
                console.print(",".join(str(value) for value in content.values()))
            else:
                console.print(content)
        return

    report = _KNOWN_REPORTS.get(report_id)
    if report is not None:
        if fmt == "json":
            console.print(json.dumps(report, indent=2))
        else:
            console.print("id,description,status")
            console.print(f"{report['id']},{report['description']},{report['status']}")
        return

    console.print(f"[red]Report not found: {report_id}[/red]")
    console.print(f"[dim]Searched: {reports_dir}[/dim]")
    sys.exit(1)
