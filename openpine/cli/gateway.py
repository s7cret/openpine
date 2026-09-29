"""CLI gateway command group."""

import click
from rich.console import Console

console = Console()


@click.group()
def gateway() -> None:
    """Web gateway commands."""


@gateway.command("run")
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=8080, type=int, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
@click.option("--workers", default=1, type=int, help="Number of workers")
def gateway_run(host: str, port: int, reload: bool, workers: int) -> None:
    """Start the OpenPine web gateway."""
    import uvicorn

    console.print(
        f"[bold green]Starting OpenPine Gateway on {host}:{port}[/bold green]"
    )
    console.print(f"  Docs: http://{host}:{port}/docs")
    console.print(f"  API:  http://{host}:{port}/api")
    uvicorn.run(
        "openpine.gateway.server:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_level="info",
    )
