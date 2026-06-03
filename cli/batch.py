"""Batch CLI commands."""

from __future__ import annotations

import click


@click.group()
def batch() -> None:
    """Batch execution commands."""
    pass


def _run_batch_phase(ctx: click.Context, phase: str) -> None:
    from openpine.batch.runner import main as batch_main

    argv = [*ctx.args, "--phase", phase]
    raise SystemExit(batch_main(argv))


def _phase_command(name: str, help_text: str):
    @batch.command(name, context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
    @click.pass_context
    def command(ctx: click.Context) -> None:
        _run_batch_phase(ctx, name)

    command.__doc__ = help_text
    return command


batch_plan = _phase_command("plan", "Plan the manifest-driven Pine export batch.")
batch_ingest = _phase_command("ingest", "Ingest Pine sources from a manifest batch.")
batch_compile = _phase_command("compile", "Compile Pine sources from a manifest batch.")
batch_register = _phase_command("register", "Register strategies from a manifest batch.")
batch_run = _phase_command("run", "Run the manifest-driven Pine export batch runner.")
