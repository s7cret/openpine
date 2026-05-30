"""Batch CLI commands."""

from __future__ import annotations

import click


@click.group()
def batch() -> None:
    """Batch execution commands."""
    pass


@batch.command("run", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.pass_context
def batch_run(ctx: click.Context) -> None:
    """Run the manifest-driven Pine export batch runner."""
    from openpine.batch.runner import main as batch_main

    argv = ["--phase", "run", *ctx.args]
    raise SystemExit(batch_main(argv))
