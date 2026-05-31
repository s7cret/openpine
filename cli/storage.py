"""Storage management CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console

console = Console()


def _fmt_utc_seconds(timestamp_seconds: int) -> str:
    return f"{datetime.fromtimestamp(timestamp_seconds, timezone.utc):%Y-%m-%d %H:%M:%S}"


@click.group()
def storage() -> None:
    """Storage management commands."""
    pass


@storage.command()
@click.option("--path", type=click.Path(), default=None)
@click.option("--dry-run", is_flag=True)
def init(path: str | None, dry_run: bool) -> None:
    """Initialize storage."""
    from openpine.config import OpenPineConfig
    from openpine.storage import MigrationRunner, SQLiteStorage

    if path is None:
        config = OpenPineConfig.load()
        db_path = config.sqlite_path
    else:
        db_path = Path(path)

    console.print(f"[bold]Storage init[/bold] - path={db_path}")
    if dry_run:
        console.print("[dim]Dry run - no changes made[/dim]")
        return

    storage_db = SQLiteStorage(db_path)
    runner = MigrationRunner()
    applied = runner.run_migrations(storage_db)
    storage_db.close()

    if applied:
        console.print(f"[green]Applied migrations: {applied}[/green]")
    else:
        console.print("[dim]No pending migrations[/dim]")
    console.print("[green]Storage initialized[/green]")


@storage.command()
@click.option("--path", type=click.Path(), default=None)
def schema(path: str | None) -> None:
    """Show storage schema."""
    from openpine.config import OpenPineConfig
    from openpine.storage import SQLiteStorage

    if path is None:
        config = OpenPineConfig.load()
        db_path = config.sqlite_path
    else:
        db_path = Path(path)

    console.print(f"[bold]Storage schema[/bold] - path={db_path}")

    if not db_path.exists():
        console.print(f"[red]Database not found: {db_path}[/red]")
        console.print("Run 'openpine storage init' first.")
        return

    storage_db = SQLiteStorage(db_path)
    cursor = storage_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    storage_db.close()

    console.print(f"Tables ({len(tables)}): {', '.join(tables)}")
    for table in tables:
        console.print(f"\n  [bold]{table}[/bold]")
        table_storage = SQLiteStorage(db_path)
        col_cursor = table_storage.execute(f"PRAGMA table_info({table})")
        for col in col_cursor.fetchall():
            console.print(f"    {col[1]} {col[2]}  nullable={not col[3]} default={col[4]}")
        table_storage.close()


@storage.command("migrate")
@click.option("--path", type=click.Path(), default=None)
def migrate(path: str | None) -> None:
    """Run pending migrations."""
    from openpine.config import OpenPineConfig
    from openpine.storage import MigrationRunner, SQLiteStorage

    if path is None:
        config = OpenPineConfig.load()
        db_path = config.sqlite_path
    else:
        db_path = Path(path)

    console.print(f"[bold]Storage migrate[/bold] - path={db_path}")

    storage_db = SQLiteStorage(db_path)
    runner = MigrationRunner()
    applied = runner.run_migrations(storage_db)
    storage_db.close()

    table_storage = SQLiteStorage(db_path)
    cursor = table_storage.execute(
        "SELECT version, name, applied_at, description FROM schema_migrations ORDER BY id"
    )
    rows = cursor.fetchall()
    table_storage.close()

    if rows:
        console.print(f"[bold]Applied migrations ({len(rows)})[/bold]")
        for version, name, applied_at, description in rows:
            ts = _fmt_utc_seconds(applied_at)
            console.print(f"  {version}  {name}  - {description}  [{ts}]")
    else:
        console.print("[dim]No migrations applied yet[/dim]")

    if applied:
        console.print(f"[green]Newly applied: {applied}[/green]")
    else:
        console.print("[dim]No pending migrations[/dim]")


@storage.command("backup")
@click.option("--out", required=True, type=click.Path(), help="Output .tar.gz path")
def backup(out: str) -> None:
    """Create OpenPine backup archive."""
    from openpine.config import OpenPineConfig
    from openpine.storage.backup import backup_openpine

    config = OpenPineConfig.load()
    out_path = Path(out)
    console.print(f"[bold]Creating backup[/bold] -> {out_path}")
    try:
        backed = backup_openpine(out_path, config)
        console.print(f"[green]Backup complete[/green] - {len(backed)} items:")
        for item in backed:
            console.print(f"  {item}")
    except Exception as exc:
        console.print(f"[red]Backup failed: {exc}[/red]")
        raise SystemExit(1) from exc


@storage.command("restore")
@click.argument("backup_path", type=click.Path(exists=True))
@click.option("--target", type=click.Path(), default=None, help="Target data directory")
def restore(backup_path: str, target: str | None) -> None:
    """Restore from OpenPine backup archive."""
    from openpine.storage.backup import restore_openpine

    backup_path_obj = Path(backup_path)
    target_path = Path(target) if target else None
    console.print(f"[bold]Restoring backup[/bold] from {backup_path_obj}")
    try:
        restore_openpine(backup_path_obj, target_path)
        console.print("[green]Restore complete[/green]")
    except Exception as exc:
        console.print(f"[red]Restore failed: {exc}[/red]")
        raise SystemExit(1) from exc


@storage.command("verify")
def verify() -> None:
    """Verify storage integrity."""
    from openpine.config import OpenPineConfig
    from openpine.storage.backup import verify_openpine

    config = OpenPineConfig.load()
    console.print("[bold]Verifying storage integrity[/bold]")
    results = verify_openpine(config)
    critical_checks = {"sqlite_exists", "sqlite_integrity"}
    critical_failed = False
    warnings = []
    for name, passed in results.items():
        icon = "[green]✓[/green]" if passed else "[red]✗[/red]"
        if not passed and name in critical_checks:
            critical_failed = True
        elif not passed:
            warnings.append(name)
        console.print(f"  {icon} {name}: {passed}")

    if critical_failed:
        console.print("[red]Critical storage checks failed[/red]")
        raise SystemExit(1)
    if warnings:
        console.print(f"[yellow]Warnings:[/yellow] {', '.join(warnings)}")
        console.print("[green]Critical checks passed[/green]")
    else:
        console.print("[green]All checks passed[/green]")


storage.add_command(init, "storage-init")
storage.add_command(schema, "storage-schema")
