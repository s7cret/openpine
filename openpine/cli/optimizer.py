"""Optimizer CLI commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console

console = Console()


@click.group()
def optimizer() -> None:
    """Optimization commands."""
    pass


@optimizer.command("dry-run")
@click.option("--strategy", "strategy_id", required=True, help="Strategy ID")
@click.option("--trials", required=True, type=int, help="Number of trials to plan")
def optimizer_dry_run(strategy_id: str, trials: int) -> None:
    """Validate optimizer routing without launching external work."""
    from openpine.optimizer import OptimizerService

    if trials < 1:
        console.print("[red]--trials must be >= 1[/red]")
        sys.exit(1)

    result = OptimizerService().validate_config(strategy_id=strategy_id, trials=trials)
    console.print("[bold]Optimizer config validation[/bold]")
    console.print(f"strategy_id:               {result.strategy_id}")
    console.print(f"trials_requested:          {result.trials_requested}")
    console.print(f"status:                    {result.status}")
    if result.reason:
        console.print(f"reason:                    {result.reason}")
