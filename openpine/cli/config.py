"""Configuration CLI commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console

from openpine.timezones import resolve_timezone

console = Console()


@click.group(name="config")
def config() -> None:
    """Configuration management commands."""
    pass


@config.command("show")
def config_show() -> None:
    """Show current OpenPine configuration."""
    from openpine.config import OpenPineConfig

    cfg = OpenPineConfig.load()
    console.print("[bold]OpenPine Configuration[/bold]")
    console.print(f"  workspace_root: {cfg.workspace_root}")
    console.print(f"  data_cache_root:{cfg.data_cache_root}")
    console.print(f"  output_root:    {cfg.output_root}")
    console.print(f"  db_path:        {cfg.db_path}")
    console.print(f"  data_dir:       {cfg.data_dir}")
    console.print(f"  config_dir:     {cfg.config_dir}")
    console.print(f"  sqlite_path:    {cfg.sqlite_path}")
    console.print(f"  duckdb_path:    {cfg.duckdb_path}")
    console.print(f"  log_level:      {cfg.log_level}")
    tz = resolve_timezone(cfg.timezone)
    console.print(f"  timezone:       {tz.name} ({tz.label})")
    console.print(f"  live_enabled:   {cfg.live_enabled}")
    console.print(f"  kill_switch:    {cfg.kill_switch}")
    console.print(f"  plugins.telegram.enabled: {cfg.plugins.telegram.enabled}")
    console.print(f"  plugins.telegram.token_ref: {cfg.plugins.telegram.token_ref}")


@config.command("validate")
def config_validate() -> None:
    """Validate the current OpenPine configuration."""
    from openpine.config import OpenPineConfig

    cfg = OpenPineConfig.load()
    errors: list[str] = []

    required_paths = {
        "workspace_root": cfg.workspace_root,
        "data_cache_root": cfg.data_cache_root,
        "output_root": cfg.output_root,
        "db_path": cfg.db_path,
        "data_dir": cfg.data_dir,
        "config_dir": cfg.config_dir,
        "sqlite_path": cfg.sqlite_path,
    }
    for name, value in required_paths.items():
        if value is None:
            errors.append(f"{name} is required")

    if not isinstance(cfg.live_enabled, bool):
        errors.append("live_enabled must be a boolean")
    if not isinstance(cfg.kill_switch, bool):
        errors.append("kill_switch must be a boolean")
    if not isinstance(cfg.log_level, str):
        errors.append("log_level must be a string")
    try:
        resolve_timezone(cfg.timezone)
    except ValueError as exc:
        errors.append(str(exc))
    if cfg.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        errors.append(
            f"log_level must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got {cfg.log_level}"
        )

    console.print("[bold]Config Validation[/bold]")
    if errors:
        console.print("[red]Validation failed:[/red]")
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        sys.exit(1)
    console.print("[green]Configuration is valid.[/green]")
