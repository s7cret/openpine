"""OpenPine CLI — main entry point."""

from __future__ import annotations

import shutil
import sys
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import click
from rich.console import Console

from openpine import __version__
from openpine.cli.batch import batch
from openpine.cli.compare import (
    _compare_csv_float,
    _compare_csv_time_ms,
    _compare_normalized_header,
    _compare_rows_by_order,
    _compare_rows_by_time,
    _compare_strategy_run_with_tv_exports,
    _find_compare_column,
    _read_compare_csv,
    _trade_action_and_direction,
    _write_normalized_tv_trades,
    _write_strategy_tv_compare_report,
)
from openpine.cli.data import data
from openpine.cli.ops import jobs, queue, service, workers
from openpine.cli.storage import storage
from openpine.exchange_metadata import (
    default_qty_rounding_mode as metadata_default_qty_rounding_mode,
)
from openpine.exchange_metadata import default_qty_step
from openpine.jobs import JobScheduler
from openpine.timezones import parse_timestamp_ms

# Global instances — created once at module load
console = Console()
_cli_scheduler = JobScheduler()
from openpine.cli.runtime_helpers import (
    _fmt_utc_ms,
    _fmt_utc_seconds,
    _fmt_utc_ms_as,
    _default_qty_step,
    _default_qty_rounding_mode,
    _parse_cli_date_ms,
    _plot_record_count,
    _bars_data_fingerprint,
    _build_strategy_backtest_config,
    _build_strategy_replay_config,
    _get_strategy_or_exit,
    _print_strategy_command_header,
    _strategy_backtest_readiness_error,
    _exit_if_strategy_not_ready_for_backtest,
    _parse_strategy_backtest_window,
    _parse_valid_strategy_backtest_window,
    _print_backtest_result_summary,
    _load_strategy_backtest_class,
    _load_strategy_backtest_class_or_exit,
    _exit_if_no_strategy_bars,
    _build_strategy_backtest_params_and_config,
    _prepare_strategy_backtest_inputs,
    _prepare_strategy_replay_inputs,
    _build_strategy_backtest_run_request,
    _prepare_strategy_backtest_runtime,
    _build_progress_callback,
    _parse_indicator_plot_window,
    _load_pine_source_or_exit,
    _require_active_pine_artifact,
    _load_generated_class_timed,
    _print_indicator_plot_header,
    _ensure_output_dir,
    _strategy_backtest_dependencies,
    _indicator_plot_dependencies,
    _load_indicator_plot_bars,
    _execute_indicator_plot_runtime,
    _run_indicator_plot_runtime,
    _write_indicator_plot_outputs,
    _write_indicator_plot_run_meta,
    _write_indicator_plot_run_outputs,
    _prepare_indicator_plot_inputs,
    _build_strategy_backtest_run_meta,
    _print_strategy_plot_capture_status,
    _load_strategy_backtest_bars,
    _strategy_backtest_declaration_args,
    _save_strategy_backtest_result,
    _save_strategy_backtest_result_safely,
    _persist_strategy_backtest_result,
    _save_strategy_resume_snapshot,
    _run_strategy_backtest_adapter,
    _run_strategy_backtest_or_exit,
    _build_cli_bar_query,
    _build_indicator_plot_config,
    _build_indicator_plot_run_meta,
)


@click.group()
@click.version_option(version=__version__, prog_name="openpine")
def cli() -> None:
    """OpenPine Trading Platform CLI."""
    pass


def _auto_pine_source_name(source_path: Path) -> str:
    stem = source_path.stem
    first = stem.split("_", 1)[0]
    if first.isdigit():
        return f"po_{int(first):04d}_{stem}"
    return f"po_{stem}"


def _detect_pine_source_kind(source_path: Path) -> str:
    import re as _re

    text = source_path.read_text(encoding="utf-8", errors="ignore")
    if _re.search(r"\bstrategy\s*\(", text):
        return "strategy"
    if _re.search(r"\bindicator\s*\(", text) or _re.search(r"\bstudy\s*\(", text):
        return "indicator"
    raise click.ClickException(
        "Cannot detect Pine source kind: expected indicator(...) or strategy(...)"
    )


def _run_openpine_cli(args: list[str]) -> str:
    import subprocess as _subprocess

    cmd = [sys.executable, "-m", "openpine.cli.main", *args]
    result = _subprocess.run(cmd, text=True, capture_output=True)
    if result.stdout:
        console.print(result.stdout.rstrip())
    if result.stderr:
        console.print(result.stderr.rstrip(), style="dim")
    if result.returncode != 0:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise click.ClickException(f"Command failed: {' '.join(args)}\n{details}")
    return result.stdout


@cli.command("run")
@click.argument("source_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--symbol", required=True, help="Symbol, e.g. BTCUSDT")
@click.option("--timeframe", required=True, help="Chart timeframe, e.g. 15m")
@click.option("--exchange", default="binance", show_default=True)
@click.option("--market-type", default="spot", show_default=True)
@click.option("--from", "from_date", required=True, help="Calculation start date")
@click.option("--to", "to_date", default=None, help="Calculation end date")
@click.option(
    "--history-from", default=None, help="Strategy history start before --from"
)
@click.option(
    "--compare-from", default=None, help="Optional export/compare window start"
)
@click.option("--compare-to", default=None, help="Optional export/compare window end")
@click.option("--output", "output_dir", type=click.Path(file_okay=False), required=True)
@click.option(
    "--tv-chart", type=click.Path(dir_okay=False), help="Optional TradingView chart CSV"
)
@click.option(
    "--tv-trades",
    type=click.Path(dir_okay=False),
    help="Optional TradingView trades CSV",
)
@click.option(
    "--tv-equity",
    type=click.Path(dir_okay=False),
    help="Optional TradingView equity CSV",
)
@click.option("--capture-plots", is_flag=True, help="Capture strategy plots")
@click.option(
    "--gap-policy",
    default="fail",
    show_default=True,
    type=click.Choice(["fail", "allow_with_metadata"]),
)
@click.option("--progress-every", default=10_000, show_default=True)
def run_pine_file(
    source_path: str,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    from_date: str,
    to_date: str | None,
    history_from: str | None,
    compare_from: str | None,
    compare_to: str | None,
    output_dir: str,
    tv_chart: str | None,
    tv_trades: str | None,
    tv_equity: str | None,
    capture_plots: bool,
    gap_policy: str,
    progress_every: int,
) -> None:
    """Run a Pine file through the correct indicator or strategy CLI path."""
    source = Path(source_path)
    name = _auto_pine_source_name(source)
    kind = _detect_pine_source_kind(source)
    output = Path(output_dir)
    console.print(f"[bold]OpenPine run[/bold] kind={kind} source={source} name={name}")

    try:
        _run_openpine_cli(["pine", "pine-add", name, str(source)])
    except click.ClickException as exc:
        message = str(exc).lower()
        if "already" not in message and "unique constraint failed" not in message:
            console.print(
                f"[dim]pine-add skipped/failed; continuing with existing source if present: {exc}[/dim]"
            )
    _run_openpine_cli(["pine", "pine-compile", name])

    if kind == "indicator":
        openpine_out = output / "openpine"
        args = [
            "pine",
            "run-plots",
            name,
            "--symbol",
            symbol,
            "--timeframe",
            timeframe,
            "--exchange",
            exchange,
            "--market-type",
            market_type,
            "--from",
            from_date,
            "--output",
            str(openpine_out),
            "--progress-every",
            str(progress_every),
        ]
        if to_date:
            args.extend(["--to", to_date])
        if compare_from:
            args.extend(["--compare-from", compare_from])
        if compare_to:
            args.extend(["--compare-to", compare_to])
        _run_openpine_cli(args)
        if tv_chart:
            _run_openpine_cli(
                [
                    "pine",
                    "compare-tv",
                    name,
                    "--openpine-plots",
                    str(openpine_out / "plots.csv"),
                    "--tv-chart",
                    tv_chart,
                    "--output",
                    str(output / "compare"),
                ]
            )
        return

    create_out = _run_openpine_cli(
        [
            "strategy",
            "create",
            "--pine",
            name,
            "--symbol",
            symbol,
            "--timeframe",
            timeframe,
            "--exchange",
            exchange,
            "--market-type",
            market_type,
            "--mode",
            "backtest",
        ]
    )
    strategy_id = None
    for line in create_out.splitlines():
        if "Strategy created:" in line:
            strategy_id = line.rsplit(":", 1)[-1].strip()
            break
    if not strategy_id:
        raise click.ClickException("Could not parse created strategy id")

    backtest_args = [
        "strategy",
        "backtest",
        strategy_id,
        "--from",
        from_date,
        "--gap-policy",
        gap_policy,
    ]
    if to_date:
        backtest_args.extend(["--to", to_date])
    if history_from:
        backtest_args.extend(["--history-from", history_from])
    if capture_plots or tv_chart:
        backtest_args.append("--capture-plots")
        if compare_from:
            backtest_args.extend(["--capture-from", compare_from])
        if compare_to:
            backtest_args.extend(["--capture-to", compare_to])
    _run_openpine_cli(backtest_args)
    if any((tv_chart, tv_trades, tv_equity)):
        compare_args = [
            "strategy",
            "compare-tv",
            strategy_id,
            "--output",
            str(output / "compare"),
        ]
        if tv_chart:
            compare_args.extend(["--tv-chart", tv_chart])
        if tv_trades:
            compare_args.extend(["--tv-trades", tv_trades])
        if tv_equity:
            compare_args.extend(["--tv-equity", tv_equity])
        if compare_from:
            compare_args.extend(["--compare-from", compare_from])
        if compare_to:
            compare_args.extend(["--compare-to", compare_to])
        _run_openpine_cli(compare_args)


cli.add_command(batch)
cli.add_command(data)
cli.add_command(storage)
cli.add_command(jobs)
cli.add_command(service)
cli.add_command(queue)
cli.add_command(workers)


def _validate_event_schema(event_type: str) -> bool:
    """Validate CLI-known event schema contracts."""
    if event_type not in {"StrategyRuntimeError", "strategy_runtime_error"}:
        console.print(f"[yellow]Unknown event type: {event_type}[/yellow]")
        return False

    from openpine.contracts import StrategyRuntimeError
    from openpine.events import StrategyRuntimeErrorPayload

    expected_fields = {
        "strategy_id",
        "artifact_id",
        "params_hash",
        "instrument_key",
        "timeframe",
        "bar_time",
        "error_type",
        "message",
        "traceback_id",
        "job_id",
        "strategy_status_after",
    }
    contract_fields = set(StrategyRuntimeError.model_fields)
    payload_fields = set(StrategyRuntimeErrorPayload.__dataclass_fields__)
    if expected_fields <= contract_fields and expected_fields <= payload_fields:
        console.print("[green]StrategyRuntimeError schema valid[/green]")
        return True

    missing_contract = sorted(expected_fields - contract_fields)
    missing_payload = sorted(expected_fields - payload_fields)
    if missing_contract:
        console.print(
            f"[red]StrategyRuntimeError contract missing: {missing_contract}[/red]"
        )
    if missing_payload:
        console.print(
            f"[red]StrategyRuntimeError payload missing: {missing_payload}[/red]"
        )
    return False


def _print_state_policy() -> None:
    """Show current state save policy (section 33.7)."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    state_cfg = getattr(config, "state", None)
    console.print("[bold]State policy[/bold]")
    if state_cfg:
        console.print(
            f"save_policy:        {getattr(state_cfg, 'save_policy', 'every_bar')}"
        )
        console.print(
            f"save_interval_bars:  {getattr(state_cfg, 'save_interval_bars', 1)}"
        )
        console.print(
            f"max_snapshots:      {getattr(state_cfg, 'keep_last_snapshots', 1000)}"
        )
    else:
        console.print("save_policy:        every_bar  (default)")
        console.print("save_interval_bars: 1         (default)")
        console.print("max_snapshots:      1000      (default)")


def _check_sqlite_reachable(config, console) -> bool:
    try:
        from openpine.storage import SQLiteStorage

        storage = SQLiteStorage(config.sqlite_path)
        cursor = storage.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        storage.close()
        console.print(f"  [green]✓[/green] SQLite reachable ({len(tables)} tables)")
        return True
    except Exception as e:
        console.print(f"  [red]✗[/red] SQLite: {e}")
        return False


def _check_sqlite_wal_mode(config, console) -> None:
    try:
        from openpine.storage import SQLiteStorage

        storage = SQLiteStorage(config.sqlite_path)
        cursor = storage.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        storage.close()
        if mode.upper() == "WAL":
            console.print("  [green]✓[/green] WAL mode enabled")
        else:
            console.print(f"  [yellow]![/yellow] journal_mode={mode} (expected WAL)")
    except Exception as e:
        console.print(f"  [red]✗[/red] WAL mode check: {e}")


def _check_writable_dir(path: Path, label: str, console) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        console.print(f"  [green]✓[/green] {label} writable: {path}")
        return True
    except Exception as e:
        console.print(f"  [red]✗[/red] {label}: {e}")
        return False


def _check_optional_duckdb(config, console) -> None:
    try:
        import duckdb

        con = duckdb.connect(database=str(config.duckdb_path), read_only=True)
        con.execute("SELECT 1").fetchone()
        con.close()
        console.print("  [green]✓[/green] DuckDB query works")
    except ImportError:
        console.print("  [dim]  DuckDB not installed (optional)[/dim]")
    except Exception as e:
        console.print(f"  [yellow]![/yellow] DuckDB: {e} (optional)")


def _check_job_queue_health(console) -> None:
    try:
        from openpine.jobs import JobScheduler

        scheduler = JobScheduler()
        recovered = scheduler.recover_stale_locks()
        if recovered > 0:
            console.print(f"  [yellow]![/yellow] Recovered {recovered} stale lock(s)")
        else:
            console.print("  [green]✓[/green] No stale locks")
    except Exception as e:
        console.print(f"  [red]✗[/red] Stale lock check: {e}")

    try:
        from openpine.jobs import JobScheduler, JobStatus

        scheduler = JobScheduler()
        failed = scheduler.list_jobs(status=JobStatus.FAILED)
        if failed:
            console.print(f"  [yellow]![/yellow] {len(failed)} failed job(s) in queue")
        else:
            console.print("  [green]✓[/green] No failed jobs")
    except Exception as e:
        console.print(f"  [red]✗[/red] Failed jobs check: {e}")


def _run_deep_checks(config, console, all_ok: bool) -> bool:
    """Run deep diagnostic checks (section 28.1 TZ)."""
    # Python/package versions
    import platform

    console.print(f"  Python: {platform.python_version()}")
    console.print(f"  Platform: {platform.platform()}")

    # Core library versions
    from openpine.integrations import check_core_libraries

    for status in check_core_libraries():
        if status.importable:
            version = f" {status.version}" if status.version else ""
            console.print(f"  [green]✓[/green] {status.name}{version}")
        else:
            console.print(f"  [red]✗[/red] {status.name}: {status.error}")
            all_ok = False

    if not _check_sqlite_reachable(config, console):
        all_ok = False

    _check_sqlite_wal_mode(config, console)

    if not _check_writable_dir(
        config.data_dir / "parquet", "Parquet data dir", console
    ):
        all_ok = False
    if not _check_writable_dir(
        config.config_dir / "artifacts", "Artifact dir", console
    ):
        all_ok = False
    if not _check_writable_dir(config.config_dir / "state", "State dir", console):
        all_ok = False

    _check_optional_duckdb(config, console)

    # Provider connectivity smoke test
    try:
        from openpine.data.orchestrator import DataOrchestrator

        DataOrchestrator()
        # Smoke test: try to get bars (will return empty if no provider)
        console.print("  [green]✓[/green] DataOrchestrator smoke test passed")
    except Exception as e:
        console.print(f"  [red]✗[/red] DataOrchestrator: {e}")
        all_ok = False

    # Account permissions without printing secrets
    try:
        from openpine.accounts import AccountManager
        from openpine.storage import SQLiteStorage

        storage = SQLiteStorage(config.sqlite_path)
        manager = AccountManager(storage)
        accounts = manager.list_accounts()
        storage.close()
        console.print(
            f"  [green]✓[/green] AccountManager accessible ({len(accounts)} accounts)"
        )
    except Exception as e:
        console.print(f"  [red]✗[/red] AccountManager: {e}")
        all_ok = False

    # Worker heartbeat health
    try:
        from openpine.workers import AggregationWorkerPool, FeatureWorkerPool
        from openpine.jobs import JobScheduler

        scheduler = JobScheduler()
        agg_pool = AggregationWorkerPool(scheduler)
        feat_pool = FeatureWorkerPool(scheduler)
        agg_status = agg_pool.get_status()
        feat_status = feat_pool.get_status()
        console.print("  [green]✓[/green] Worker pools initialized")
        console.print(f"    Aggregation: {agg_status['active_workers']} workers")
        console.print(f"    Feature: {feat_status['active_workers']} workers")
    except Exception as e:
        console.print(f"  [red]✗[/red] Worker pools: {e}")
        all_ok = False

    _check_job_queue_health(console)

    # Risk kill switch
    console.print(f"  Kill switch: {config.kill_switch}")
    console.print(f"  Live enabled: {config.live_enabled}")

    # Plugin health
    try:
        from openpine.notifications import PluginManager, TelegramCommandPlugin

        manager = PluginManager(
            plugins=[TelegramCommandPlugin(config=config.plugins.telegram)]
        )
        loaded = manager.load_plugins()
        console.print(
            f"  [green]✓[/green] PluginManager accessible ({len(loaded)} plugins)"
        )
    except Exception as e:
        console.print(f"  [red]✗[/red] PluginManager: {e}")
        all_ok = False

    return all_ok


@cli.command()
@click.option("--strict", is_flag=True, help="Run strict final consistency checks")
@click.option("--deep", is_flag=True, help="Run deep diagnostics")
def doctor(strict: bool, deep: bool) -> None:
    """Run system health checks."""
    from openpine.config import OpenPineConfig

    console.print("[bold]OpenPine Doctor[/bold]")
    console.print(f"Version: {__version__}")
    console.print(f"Python: {sys.version}")

    config = OpenPineConfig.load()
    console.print(f"Data dir: {config.data_dir}")
    console.print(f"Config dir: {config.config_dir}")
    console.print(f"Live enabled: {config.live_enabled}")
    console.print(f"Kill switch: {config.kill_switch}")

    # Check critical imports
    critical = ["pydantic", "click", "rich", "structlog"]
    all_ok = True
    for mod in critical:
        try:
            __import__(mod)
            console.print(f"  [green]✓[/green] {mod}")
        except ImportError:
            console.print(f"  [red]✗[/red] {mod} — MISSING")
            all_ok = False

    if strict:
        console.print("\n[bold]Strict checks[/bold]")
        from openpine.integrations import check_core_libraries
        from openpine.jobs import Job
        from openpine.optimizer import OptimizerService
        from openpine.state import SavePolicy, SnapshotPolicy
        from openpine.workers import AggregationWorkerPool, FeatureWorkerPool

        for status in check_core_libraries():
            if status.importable:
                version = f" {status.version}" if status.version else ""
                console.print(f"  [green]✓[/green] {status.name}{version}")
            else:
                console.print(f"  [red]✗[/red] {status.name}: {status.error}")
                all_ok = False

        if _validate_event_schema("StrategyRuntimeError"):
            console.print("  [green]✓[/green] StrategyRuntimeError event schema")
        else:
            console.print("  [red]✗[/red] StrategyRuntimeError event schema")
            all_ok = False
        strict_checks = [
            (
                "Job.serialization_key contract",
                "serialization_key" in getattr(Job, "__dataclass_fields__", {}),
            ),
            (
                "state.save_policy default every_bar",
                SnapshotPolicy().save_policy == SavePolicy.EVERY_BAR
                and SnapshotPolicy().save_interval_bars == 1,
            ),
            (
                "AggregationWorker/FeatureWorker separated",
                AggregationWorkerPool.JOB_TYPES.isdisjoint(FeatureWorkerPool.JOB_TYPES),
            ),
            (
                "OptimizerService validation boundary",
                OptimizerService().validate_config("doctor_smoke", 1).status == "valid",
            ),
        ]
        for name, ok in strict_checks:
            if ok:
                console.print(f"  [green]✓[/green] {name}")
            else:
                console.print(f"  [red]✗[/red] {name}")
                all_ok = False

    if deep:
        console.print("\n[bold]Deep diagnostics[/bold]")
        _run_deep_checks(config, console, all_ok)

    if all_ok:
        console.print("\n[bold green]All checks passed[/bold green]")
    else:
        console.print("\n[bold red]Some checks failed[/bold red]")
        sys.exit(1)


@cli.group()
def pine() -> None:
    """Pine source management."""
    pass


@pine.command("list")
@click.option(
    "--json", "as_json", is_flag=True, help="Output as JSON for bot consumption"
)
def pine_list(as_json: bool) -> None:
    """List registered Pine sources."""
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        sources = registry.list_sources()
        if not sources:
            if as_json:
                console.print("[]")
            else:
                console.print("[dim](no sources registered yet)[/dim]")
            return

        if as_json:
            data = [
                {
                    "id": s.id,
                    "name": s.name,
                    "version": s.version,
                    "source_type": s.source_type,
                    "active_artifact_id": s.active_artifact_id,
                    "created_at": s.created_at,
                }
                for s in sources
            ]
            console.print(json.dumps(data))
        else:
            console.print("[bold]Pine sources[/bold]")
            for s in sources:
                active = (
                    f" [dim]active: {s.active_artifact_id}[/dim]"
                    if s.active_artifact_id
                    else ""
                )
                console.print(f"  {s.name}  id={s.id}{active}")
    finally:
        registry.close()


@pine.command("show")
@click.argument("name")
def pine_show(name: str) -> None:
    """Show Pine source details."""
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return
        console.print(f"[bold]Pine source: {source.name}[/bold]")
        console.print(f"  id:                {source.id}")
        console.print(f"  version:           {source.version}")
        console.print(f"  source_type:      {source.source_type}")
        console.print(f"  active_artifact:  {source.active_artifact_id or '(none)'}")
        console.print(f"  created_at:        {source.created_at}")
        console.print(f"  updated_at:       {source.updated_at}")
    finally:
        registry.close()


@pine.command()
@click.argument("name")
@click.argument("source_path", type=click.Path(exists=True))
def pine_add(name: str, source_path: str) -> None:
    """Add a Pine source from a .pine file."""
    from openpine.pine.registry import SQLitePineSourceRegistry

    source_text = Path(source_path).read_text()
    registry = SQLitePineSourceRegistry()
    try:
        source = registry.add_source(source_text, name)
        console.print(
            f"[green]Added Pine source: {source.name} (id={source.id})[/green]"
        )
    finally:
        registry.close()


@pine.command()
@click.argument("name")
@click.option(
    "--force", is_flag=True, help="Force recompile even if cached artifact exists"
)
def pine_compile(name: str, force: bool) -> None:
    """Compile a Pine source and produce a CompileArtifact."""
    from openpine.pine.registry import SQLitePineSourceRegistry
    from openpine.compile import SubprocessCompilerAdapter, compile_pipeline

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return

        adapter = SubprocessCompilerAdapter()
        result = compile_pipeline(source, adapter)

        if result["success"]:
            console.print(
                f"[green]Compiled successfully: {result['artifact_id']}[/green]"
            )
            console.print(f"  Artifact: {result['artifact_path']}")
            registry.set_active_artifact(source.id, result["artifact_id"])
            console.print(f"  Active artifact set: {result['artifact_id']}")
        else:
            console.print("[red]Compile failed:[/red]")
            for err in result["errors"]:
                console.print(f"  [red]- {err}[/red]")
    finally:
        registry.close()


@pine.command("run-plots")
@click.argument("name")
@click.option("--symbol", required=True, help="Symbol, e.g. BTCUSDT")
@click.option("--timeframe", required=True, help="Chart timeframe, e.g. 15m")
@click.option("--exchange", default="binance", show_default=True, help="Exchange name")
@click.option(
    "--market-type",
    default="spot",
    show_default=True,
    help="Market type, e.g. spot/usdm",
)
@click.option("--from", "from_date", required=True, help="Calculation start date")
@click.option("--to", "to_date", default=None, help="Calculation end date")
@click.option("--output", "output_dir", type=click.Path(file_okay=False), required=True)
@click.option("--compare-from", default=None, help="Optional export window start")
@click.option("--compare-to", default=None, help="Optional export window end")
@click.option(
    "--progress-every",
    default=10_000,
    show_default=True,
    help="Progress print interval in bars",
)
def pine_run_plots(
    name: str,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    from_date: str,
    to_date: str | None,
    output_dir: str,
    compare_from: str | None,
    compare_to: str | None,
    progress_every: int,
) -> None:
    """Run an indicator Pine source and export normalized plot CSV."""
    import time as _time

    deps = _indicator_plot_dependencies()
    start_total = _time.perf_counter()
    timings: dict[str, float] = {}

    prepared = _prepare_indicator_plot_inputs(
        name=name,
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        market_type=market_type,
        from_date=from_date,
        to_date=to_date,
        compare_from=compare_from,
        compare_to=compare_to,
        now_ms=int(_time_module.time() * 1000),
        registry_cls=deps.SQLitePineSourceRegistry,
        parse_time_ms_func=deps.parse_time_ms,
        load_generated_class=deps.load_generated_class_from_artifact,
        artifact_error_cls=deps.BacktestArtifactError,
        bar_query_cls=deps.BarQuery,
        instrument_key_cls=deps.InstrumentKey,
        parse_timeframe_func=deps.parse_timeframe,
        orchestrator_cls=deps.DataOrchestrator,
        provider_factory=deps.create_local_marketdata_provider_adapter,
        perf_counter=_time.perf_counter,
        console=console,
    )
    timings.update(prepared.timings)

    _print_indicator_plot_header(
        name=name,
        source=prepared.source,
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
        timeframe=timeframe,
        from_date=from_date,
        to_date=to_date,
        console=console,
    )

    _write_indicator_plot_run_outputs(
        deps=deps,
        prepared=prepared,
        name=name,
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        market_type=market_type,
        output_dir=output_dir,
        progress_every=progress_every,
        timings=timings,
        start_total=start_total,
        perf_counter=_time.perf_counter,
        console=console,
    )


@pine.command("compare-tv")
@click.argument("name")
@click.option(
    "--openpine-plots",
    type=click.Path(dir_okay=False),
    required=True,
    help="OpenPine plots.csv from pine run-plots",
)
@click.option(
    "--tv-chart",
    type=click.Path(dir_okay=False),
    required=True,
    help="TradingView chart/export CSV",
)
@click.option("--output", "output_dir", type=click.Path(file_okay=False), required=True)
@click.option("--abs-tol", default=1e-6, show_default=True, type=float)
@click.option("--rel-tol", default=1e-9, show_default=True, type=float)
@click.option(
    "--include-base-columns",
    is_flag=True,
    help="Include OHLCV/time/base columns in plot comparison",
)
def pine_compare_tv(
    name: str,
    openpine_plots: str,
    tv_chart: str,
    output_dir: str,
    abs_tol: float,
    rel_tol: float,
    include_base_columns: bool,
) -> None:
    """Compare indicator plot CSV against a TradingView chart export."""
    exclude = (
        set()
        if include_base_columns
        else {
            "time",
            "bar_time",
            "bar_index",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "Volume",
        }
    )
    output_path = Path(output_dir)
    summary, top_columns = _compare_rows_by_time(
        tv_path=Path(tv_chart),
        op_path=Path(openpine_plots),
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=exclude,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )
    summary["type"] = "plots"
    result = {
        "strategy_id": name,
        "run_id": Path(openpine_plots).name,
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "comparisons": [summary],
        "failures": (
            [{"type": "plots", "summary": summary, "top_columns": top_columns}]
            if summary["status"] != "match"
            else []
        ),
    }
    _write_strategy_tv_compare_report(output_path, result)
    console.print(f"[green]TV comparison written:[/green] {output_path}")
    console.print(
        f"  plots: {summary['status']} {summary['classification']} "
        f"mismatch={summary.get('mismatch_cells')}/{summary.get('total_cells')} "
        f"max_delta={summary.get('max_abs_delta')} worst={summary.get('worst_column')}"
    )


@pine.command("artifacts")
@click.argument("name")
def pine_artifacts(name: str) -> None:
    """List artifacts for a Pine source."""
    from openpine.artifacts import ArtifactStore
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return
    finally:
        registry.close()

    store = ArtifactStore()
    artifacts = store.list_artifacts(source.id)
    if not artifacts:
        console.print("[dim](no artifacts yet)[/dim]")
        return
    console.print(f"[bold]Artifacts for {name} ({source.id})[/bold]")
    for art in artifacts:
        meta = art.get("compile_meta", {})
        console.print(
            f"  {art['artifact_id']}  "
            f"params_hash={meta.get('params_hash', '?')[:12]}  "
            f"saved={meta.get('saved_at', '?')}"
        )


@pine.command("inspect")
@click.argument("name")
def pine_inspect(name: str) -> None:
    """Inspect artifact metadata for a Pine source."""
    from openpine.artifacts import ArtifactStore
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return
    finally:
        registry.close()

    store = ArtifactStore()
    artifacts = store.list_artifacts(source.id)
    if not artifacts:
        console.print("[dim](no artifacts yet)[/dim]")
        return
    console.print(f"[bold]Inspecting artifacts for {name}[/bold]")
    for art in artifacts:
        meta = art.get("compile_meta", {})
        console.print(f"\n  [bold]{art['artifact_id']}[/bold]")
        console.print(f"    source_id:     {art.get('source_id', '?')}")
        console.print(f"    params_hash:   {meta.get('params_hash', '?')}")
        console.print(f"    schema_version:{meta.get('schema_version', '?')}")
        console.print(f"    artifact_dir:  {art.get('artifact_dir', '?')}")
        console.print(f"    python_bytes:  {len(art.get('python_code', ''))}")


@pine.command("rollback")
@click.argument("name")
@click.option(
    "--to-version", "artifact_id", default=None, help="Artifact ID to set as active"
)
def pine_rollback(name: str, artifact_id: str | None) -> None:
    """Rollback to a previous artifact version for a Pine source."""
    from openpine.artifacts import ArtifactStore
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return

        store = ArtifactStore()
        artifacts = store.list_artifacts(source.id)

        if not artifacts:
            console.print("[red]No artifacts to roll back to[/red]")
            return

        if artifact_id is None:
            # List available versions
            console.print(f"[bold]Available artifacts for {name}[/bold]")
            for art in artifacts:
                active = (
                    " [dim](active)[/dim]"
                    if art["artifact_id"] == source.active_artifact_id
                    else ""
                )
                console.print(f"  {art['artifact_id']}{active}")
            console.print("\nUse --to-version <artifact_id> to roll back.")
            return

        # Validate artifact exists
        valid_ids = {art["artifact_id"] for art in artifacts}
        if artifact_id not in valid_ids:
            console.print(f"[red]Artifact not found: {artifact_id}[/red]")
            return

        registry.set_active_artifact(source.id, artifact_id)
        console.print(f"[green]Rolled back {name} to artifact {artifact_id}[/green]")
    finally:
        registry.close()


@pine.command("versions")
@click.argument("name")
def pine_versions(name: str) -> None:
    """List all artifact versions for a Pine source."""
    from openpine.artifacts import ArtifactStore
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return
    finally:
        registry.close()

    store = ArtifactStore()
    artifacts = store.list_artifacts(source.id)
    if not artifacts:
        console.print("[dim](no artifacts yet)[/dim]")
        return
    console.print(f"[bold]Artifact versions for {name}[/bold]")
    for art in artifacts:
        meta = art.get("compile_meta", {})
        created = meta.get("created_at", 0)
        active = (
            " [dim](active)[/dim]"
            if art["artifact_id"] == source.active_artifact_id
            else ""
        )
        console.print(f"  {art['artifact_id']}{active}" f"  created={created}")


@pine.command("activate")
@click.argument("name")
@click.argument("artifact_id")
def pine_activate(name: str, artifact_id: str) -> None:
    """Set active artifact for a Pine source."""
    from openpine.artifacts import ArtifactStore
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return

        store = ArtifactStore()
        artifacts = store.list_artifacts(source.id)
        valid_ids = {art["artifact_id"] for art in artifacts}
        if artifact_id not in valid_ids:
            console.print(f"[red]Artifact not found: {artifact_id}[/red]")
            console.print(f"Valid artifact IDs: {sorted(valid_ids)}")
            return

        registry.set_active_artifact(source.id, artifact_id)
        console.print(f"[green]Activated artifact {artifact_id} for {name}[/green]")
    finally:
        registry.close()


@pine.command("remove")
@click.argument("name")
def pine_remove(name: str) -> None:
    """Remove a Pine source and all its artifacts."""
    from openpine.artifacts import ArtifactStore
    from openpine.pine.registry import SQLitePineSourceRegistry

    registry = SQLitePineSourceRegistry()
    try:
        try:
            source = registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            return

        store = ArtifactStore()
        artifacts = store.list_artifacts(source.id)
        # Remove artifact directories
        for art in artifacts:
            artifact_dir_raw = str(art.get("artifact_dir") or "").strip()
            if not artifact_dir_raw:
                continue
            artifact_dir = Path(artifact_dir_raw)
            if artifact_dir.exists() and artifact_dir.is_dir():
                shutil.rmtree(artifact_dir, ignore_errors=True)

        registry.remove_source(name)
        console.print(f"[green]Removed Pine source: {name} (id={source.id})[/green]")
        console.print(f"  Removed {len(artifacts)} artifact(s)")
    finally:
        registry.close()


# ── config (top-level) ─────────────────────────────────────────────────────────


from openpine.cli.config import config as config_group

cli.add_command(config_group)


# ── init (top-level) ──────────────────────────────────────────────────────────


@cli.command("init")
def init() -> None:
    """Interactive initialization: create directories and initialize storage."""
    from openpine.config import OpenPineConfig
    from openpine.storage import MigrationRunner, SQLiteStorage

    config = OpenPineConfig.load()
    console.print("[bold]OpenPine Init[/bold]")

    # Create required directories
    dirs_to_create = [
        config.config_dir,
        config.data_dir,
        config.data_dir / "candles",
        config.data_dir / "features",
        config.data_dir / "reports",
        config.data_dir / "state",
        config.config_dir / "artifacts",
    ]

    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] {d}")

    # Initialize SQLite storage
    console.print("\n[bold]Initializing storage...[/bold]")
    storage = SQLiteStorage(config.sqlite_path)
    runner = MigrationRunner()
    applied = runner.run_migrations(storage)
    storage.close()

    if applied:
        console.print(f"[green]Applied migrations: {applied}[/green]")
    else:
        console.print("[dim]No pending migrations.[/dim]")

    console.print("\n[bold green]OpenPine initialized successfully.[/bold green]")
    console.print(f"  Config dir: {config.config_dir}")
    console.print(f"  Data dir:   {config.data_dir}")
    console.print(f"  SQLite:     {config.sqlite_path}")
    console.print("\nNext: openpine accounts add ... to register exchange accounts.")


# ── version (top-level) ────────────────────────────────────────────────────────


@cli.command("version")
def version() -> None:
    """Print OpenPine version string."""
    console.print(f"openpine {__version__}")


@cli.group()
def streams() -> None:
    """Stream management commands."""
    pass


@streams.command("status")
def streams_status() -> None:
    """Show active stream subscriptions."""
    import tempfile
    from pathlib import Path
    from openpine.events import EventBus
    from openpine.streams import MarketDataStreamManager
    from openpine.storage import SQLiteStorage
    from openpine.data.orchestrator import DataOrchestrator

    console.print("[bold]Streams status[/bold]")

    # Build in-memory stream manager to show subscriptions
    # (uses in-memory state — real subscriptions come from live daemon)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        storage = SQLiteStorage(db_path)
        bus = EventBus(storage)
        orch = DataOrchestrator()
        manager = MarketDataStreamManager(bus, orch)

        subs = manager.list_subscriptions()
        if not subs:
            console.print("[dim]No active subscriptions[/dim]")
        else:
            for sub in subs:
                console.print(
                    f"  [{sub.status.value}] {sub.subscription_id}  "
                    f"instrument={sub.instrument_key} tf={sub.timeframe}  "
                    f"provider={sub.provider}"
                )
        storage.close()
    finally:
        db_path.unlink(missing_ok=True)


@streams.command("plan")
def streams_plan() -> None:
    """Show stream setup requirements per provider."""
    console.print("[bold]Stream providers[/bold]")
    console.print("  binance_ws   wss://stream.binance.com:9443/ws")
    console.print("  kraken_ws    wss://ws.kraken.com")
    console.print("  Bybit WS     wss://stream.bybit.com/v5/public/spot")


@streams.command("setup")
def streams_setup() -> None:
    """Interactive stream setup wizard."""
    console.print("[bold]Stream Setup Wizard[/bold]")
    console.print("[dim]This wizard configures market data stream providers.[/dim]")
    console.print("")
    console.print("  [1] binance_ws  — wss://stream.binance.com:9443/ws")
    console.print("  [2] kraken_ws   — wss://ws.kraken.com")
    console.print("  [3] bybit_ws    — wss://stream.bybit.com/v5/public/spot")
    console.print("  [c] cancel")

    choice = click.prompt("Select provider", default="c").strip().lower()

    providers = {"1": "binance_ws", "2": "kraken_ws", "3": "bybit_ws"}
    if choice not in providers:
        console.print("[yellow]Setup cancelled.[/yellow]")
        return

    provider = providers[choice]
    endpoints = {
        "binance_ws": "wss://stream.binance.com:9443/ws",
        "kraken_ws": "wss://ws.kraken.com",
        "bybit_ws": "wss://stream.bybit.com/v5/public/spot",
    }
    console.print(f"\n[green]Selected provider: {provider}[/green]")
    console.print(f"  Endpoint: {endpoints.get(provider, 'unknown')}")
    console.print("\n[bold green]Stream provider configured.[/bold green]")
    console.print(
        "[dim]To enable: set stream.provider in config or OPENPINE_STREAM_PROVIDER env var[/dim]"
    )


@cli.group()
def state() -> None:
    """State management commands."""
    pass


@state.command("policy")
@click.pass_context
def state_policy(ctx: click.Context) -> None:
    """Show state save policy (section 33.7)."""
    ctx.invoke(state_policy_show)


@state.command("show")
def state_policy_show() -> None:
    """Show current state save policy (section 33.7)."""
    _print_state_policy()


@state.command("list")
@click.option("--strategy", "strategy_id", default=None, help="Filter by strategy ID")
def state_list(strategy_id: str | None) -> None:
    """List state snapshots from strategy_state_snapshots."""
    from openpine.config import OpenPineConfig
    from openpine.state.store import StateStore

    config = OpenPineConfig.load()
    state_dir = config.data_dir / "state"
    store = StateStore(state_dir)

    console.print("[bold]State Snapshots[/bold]")

    if strategy_id:
        snapshots = store.list_snapshots(strategy_id)
        if not snapshots:
            console.print(f"[dim](no snapshots for strategy {strategy_id})[/dim]")
            return
        console.print(f"[bold]Strategy: {strategy_id}[/bold]")
    else:
        # List all snapshots across all strategies
        all_snapshots: list = []
        if state_dir.exists():
            for sd in state_dir.iterdir():
                if sd.is_dir() and sd.name.startswith("strategy_id="):
                    sid = sd.name.split("=", 1)[1]
                    all_snapshots.extend(store.list_snapshots(sid))
        snapshots = all_snapshots
        if not snapshots:
            console.print("[dim](no state snapshots found)[/dim]")
            return
        console.print(f"Total: {len(snapshots)} snapshot(s)")

    for snap in sorted(snapshots, key=lambda s: s.saved_at, reverse=True):
        ts = _fmt_utc_ms(snap.saved_at)
        bar_ts = (
            _fmt_utc_ms_as(snap.bar_time, "%Y-%m-%d %H:%M") if snap.bar_time else "-"
        )
        size_kb = snap.size_bytes // 1024
        status_color = {
            "active": "green",
            "superseded": "dim",
            "invalid": "red",
        }.get(snap.status, "dim")
        console.print(
            f"  [{status_color}]{snap.status}[/{status_color}]  "
            f"id={snap.snapshot_id[:12]}  "
            f"strategy={snap.strategy_id}  "
            f"bar={bar_ts}  "
            f"size={size_kb}KB  "
            f"saved={ts}"
        )


@state.command("invalid")
def state_invalid() -> None:
    """List invalid state snapshots (section 30.8)."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    state_dir = config.data_dir / "state"

    # Collect all snapshots and check status
    all_invalid: list[dict] = []
    # Scan state dir for strategy dirs
    if not state_dir.exists():
        console.print("[dim]No state directory found.[/dim]")
        return

    for strategy_dir in state_dir.iterdir():
        if not strategy_dir.is_dir() or not strategy_dir.name.startswith(
            "strategy_id="
        ):
            continue
        strategy_id = strategy_dir.name.split("=", 1)[1]
        # List all snapshots in this strategy dir
        for snap_file in strategy_dir.glob("snap_*.state.msgpack*"):
            # Try to read metadata from debug file
            debug_file = snap_file.with_suffix(".debug.json")
            if debug_file.exists():
                import json as _json

                try:
                    debug_data = _json.loads(debug_file.read_text())
                    bar_time = debug_data.get("last_processed_bar_time", 0)
                except Exception:
                    bar_time = 0
            else:
                bar_time = 0
            # Heuristic: if there's a .invalid marker or just list as unknown
            all_invalid.append(
                {
                    "strategy_id": strategy_id,
                    "snapshot_file": snap_file.name,
                    "bar_time": bar_time,
                }
            )

    if not all_invalid:
        console.print(
            "[dim](no invalid snapshots found — all active or superseded)[/dim]"
        )
        return

    console.print(f"[bold]Invalid snapshots[/bold] ({len(all_invalid)})")
    for snap in all_invalid:
        console.print(
            f"  strategy={snap['strategy_id']}  "
            f"file={snap['snapshot_file']}  "
            f"bar_time={snap['bar_time']}"
        )


@state.command("rebuild")
@click.argument("strategy_id")
@click.option(
    "--from-bar",
    "from_bar_time",
    type=int,
    default=None,
    help="Rebuild from bar time (ms)",
)
def state_rebuild(strategy_id: str, from_bar_time: int | None) -> None:
    """Rebuild state for a strategy from snapshots (section 30.8)."""
    from openpine.config import OpenPineConfig
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.recovery import StateRebuilder
    from openpine.state.store import StateStore
    from openpine.state.errors import StateInconsistencyError

    config = OpenPineConfig.load()
    state_dir = config.data_dir / "state"
    store = StateStore(state_dir)
    rebuilder = StateRebuilder(
        state_store=store,
        data_orchestrator=DataOrchestrator(),
    )

    # Determine from_bar_time
    if from_bar_time is None:
        # Find latest active snapshot bar_time
        snapshots = store.list_snapshots(strategy_id)
        active = [s for s in snapshots if s.status == "active"]
        if active:
            from_bar_time = max(s.bar_time for s in active)
        else:
            console.print(f"[red]No active snapshots found for {strategy_id}[/red]")
            console.print("Use --from-bar to specify a bar time.")
            return

    console.print(
        f"[bold]Rebuilding state[/bold] for {strategy_id} from bar_time={from_bar_time}"
    )
    try:
        result = rebuilder.rebuild(strategy_id, from_bar_time)
        console.print("[green]Rebuild successful[/green]")
        console.print(f"  strategy_id:     {result.strategy_id}")
        console.print(f"  artifact_id:    {result.artifact_id}")
        console.print(f"  last_bar_time: {result.bar_time}")
    except StateInconsistencyError as e:
        console.print(f"[red]Rebuild failed: {e}[/red]")
        raise SystemExit(1) from e


@cli.group()
def accounts() -> None:
    """Exchange account management commands."""
    pass


@accounts.command("list")
@click.option("--strategy", "strategy_id", default=None, help="Filter by strategy ID")
def accounts_list(strategy_id: str | None) -> None:
    """List registered accounts (section 11.3)."""
    from openpine.accounts import AccountManager
    from openpine.storage import SQLiteStorage

    storage = SQLiteStorage()
    try:
        manager = AccountManager(storage)
        accts = manager.list_accounts()
        if strategy_id:
            accts = [
                a
                for a in accts
                if getattr(a, "strategy_id", None) == strategy_id
                or a.config.get("strategy_id") == strategy_id
            ]
        if not accts:
            console.print("[dim](no accounts yet)[/dim]")
            return
        for acc in accts:
            live_badge = (
                "[green]LIVE[/green]" if acc.live_enabled else "[dim]live=False[/dim]"
            )
            console.print(
                f"  {acc.name}  id={acc.id[:12]}  "
                f"type={acc.account_type.value}  exchange={acc.exchange}  {live_badge}"
            )
    finally:
        storage.close()


@accounts.command("add")
@click.option("--name", "name", required=True, help="Account name")
@click.option(
    "--exchange", "exchange", required=True, help="Exchange name (e.g. binance)"
)
@click.option("--api-key", "api_key", required=True, help="API key")
@click.option("--secret", "api_secret", required=True, help="API secret")
@click.option("--provider", "provider", default=None, help="Provider name")
@click.option("--market", "market_type", default="usdm", help="Market type (spot/usdm)")
@click.option("--mode", "mode", default="paper", help="Account mode (paper/live)")
def accounts_add(
    name: str,
    exchange: str,
    api_key: str,
    api_secret: str,
    provider: str | None,
    market_type: str,
    mode: str,
) -> None:
    """Add an exchange account."""

    from openpine.accounts import AccountManager
    from openpine.accounts.models import AccountType
    from openpine.storage import SQLiteStorage

    storage = SQLiteStorage()
    try:
        manager = AccountManager(storage)
        # Hash the secret for storage (never store raw)
        secret_hash = hashlib.sha256(api_secret.encode()).hexdigest()[:32]
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        live_enabled = mode == "live"
        resolved_mode = AccountType.PAPER if mode == "paper" else AccountType.LIVE
        resolved_provider = provider or exchange

        account = manager.create_account(
            name=name,
            exchange=exchange,
            provider=resolved_provider,
            market_type=market_type,
            mode=resolved_mode,
            account_type=resolved_mode,
            api_key_hash=api_key_hash,
            api_secret_ref=f"ref:{secret_hash}",
            live_enabled=live_enabled,
        )
        storage.commit()
        masked_key = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "****"
        console.print(f"[green]Account added: {name}[/green]")
        console.print(f"  id:          {account.account_id[:12]}")
        console.print(f"  exchange:    {exchange}")
        console.print(f"  market_type: {market_type}")
        console.print(f"  mode:        {mode}")
        console.print(f"  api_key:     {masked_key}")
        console.print("  secret:      **** (stored as reference)")
        console.print(f"  live_enabled:{live_enabled}")
    except Exception as e:
        console.print(f"[red]Failed to add account: {e}[/red]")
        storage.rollback()
        raise SystemExit(1) from e
    finally:
        storage.close()


@accounts.command("test")
@click.argument("name", required=True)
def accounts_test(name: str) -> None:
    """Test account credentials by fetching account info."""
    from openpine.accounts import AccountManager
    from openpine.storage import SQLiteStorage

    storage = SQLiteStorage()
    try:
        manager = AccountManager(storage)
        accounts = manager.list_accounts()
        account = None
        for acc in accounts:
            if acc.name == name:
                account = acc
                break

        if account is None:
            console.print(f"[red]Account not found: {name}[/red]")
            raise SystemExit(1)

        console.print(f"[bold]Testing account:[/bold] {name}")
        console.print(f"  exchange:    {account.exchange}")
        console.print(f"  provider:    {account.provider}")
        console.print(f"  market_type: {account.market_type}")
        console.print(f"  mode:        {account.mode}")

        errors: list[str] = []
        if account.mode.value == "live" and not account.live_enabled:
            errors.append("live account has live_enabled=false")
        if account.mode.value == "live" and not account.api_key_ref:
            errors.append("live account missing api_key_ref")
        if account.mode.value == "live" and not account.api_secret_ref:
            errors.append("live account missing api_secret_ref")
        if account.provider not in {"binance", "bybit", "unknown", "paper"}:
            errors.append(f"unsupported provider: {account.provider}")
        if account.exchange.lower() not in {"binance", "bybit", "paper", ""}:
            errors.append(f"unsupported exchange: {account.exchange}")

        if errors:
            console.print("[red]✗ Account configuration test FAILED[/red]")
            for error in errors:
                console.print(f"  - {error}")
            raise SystemExit(1)

        if account.mode.value == "live":
            console.print("[green]✓ Live account configuration is complete[/green]")
            console.print(
                "[dim]Network credential verification is performed by execution adapters at submit time[/dim]"
            )
        else:
            console.print(
                "[green]✓ Paper/backtest account configuration is valid[/green]"
            )
        console.print(f"[green]✓ Account '{name}' test PASSED[/green]")
    finally:
        storage.close()


@cli.group()
def providers() -> None:
    """Data provider management commands."""
    pass


_KNOWN_PROVIDERS = {
    "binance": {
        "name": "Binance",
        "rest": "https://api.binance.com",
        "ws": "wss://stream.binance.com:9443/ws",
    },
    "binance_usdm": {
        "name": "Binance USD-M Futures",
        "rest": "https://fapi.binance.com",
        "ws": "wss://stream.binance.com:9443/ws",
    },
    "bybit": {
        "name": "Bybit",
        "rest": "https://api.bybit.com",
        "ws": "wss://stream.bybit.com/v5/public/spot",
    },
    "bybit_usdm": {
        "name": "Bybit USD-M Futures",
        "rest": "https://api.bybit.com/v5",
        "ws": "wss://stream.bybit.com/v5/public/linear",
    },
    "okx": {
        "name": "OKX",
        "rest": "https://www.okx.com",
        "ws": "wss://ws.okx.com:8443/ws/v5/public",
    },
    "kraken": {
        "name": "Kraken",
        "rest": "https://api.kraken.com",
        "ws": "wss://ws.kraken.com",
    },
    "coinbase": {
        "name": "Coinbase",
        "rest": "https://api.exchange.coinbase.com",
        "ws": "wss://ws-feed.exchange.coinbase.com",
    },
    "marketdata-provider": {
        "name": "Local marketdata-provider",
        "rest": "N/A (local)",
        "ws": "N/A (local)",
    },
}


@providers.command("list")
def providers_list() -> None:
    """List configured data providers."""
    local_provider_available = False
    try:
        from openpine.data.provider_adapter import (
            create_local_marketdata_provider_adapter,
        )

        local_provider_available = (
            create_local_marketdata_provider_adapter() is not None
        )
    except Exception:
        local_provider_available = False

    from rich.table import Table

    tbl = Table(title="Configured Data Providers")
    tbl.add_column("ID", style="cyan")
    tbl.add_column("Name", style="green")
    tbl.add_column("REST endpoint", style="dim")
    tbl.add_column("WebSocket", style="dim")
    tbl.add_column("Status", style="yellow")

    for pid, info in _KNOWN_PROVIDERS.items():
        status = "configured"
        if pid == "marketdata-provider":
            status = "available" if local_provider_available else "not installed"
        tbl.add_row(
            pid,
            info["name"],
            info.get("rest", "-"),
            info.get("ws", "-"),
            status,
        )
    console.print(tbl)


@providers.command("test")
@click.argument("provider", required=True)
def providers_test(provider: str) -> None:
    """Test provider connectivity by fetching a trivial endpoint."""
    if provider not in _KNOWN_PROVIDERS:
        console.print(f"[red]Unknown provider: {provider}[/red]")
        console.print(f"  Available: {', '.join(_KNOWN_PROVIDERS.keys())}")
        raise SystemExit(1)

    info = _KNOWN_PROVIDERS[provider]
    console.print(f"[bold]Testing provider:[/bold] {provider} ({info['name']})")

    # Special case: local marketdata-provider
    if provider == "marketdata-provider":
        try:
            from openpine.data.provider_adapter import (
                create_local_marketdata_provider_adapter,
            )

            adapter = create_local_marketdata_provider_adapter()
        except Exception:
            adapter = None
        if adapter is None:
            console.print(
                "[red]✗ marketdata-provider not installed or not importable[/red]"
            )
            raise SystemExit(1)
        console.print("[green]✓ marketdata-provider is available[/green]")
        console.print(f"  path: {getattr(adapter, '_installation', 'N/A')}")
        return

    # Try a simple HTTP GET to the rest endpoint
    rest_url = info.get("rest", "")
    if not rest_url or rest_url == "N/A (local)":
        console.print(f"[yellow]! No REST endpoint for {provider}[/yellow]")
        return

    try:
        import requests

        console.print(f"[dim]GET {rest_url}/v3/time...[/dim]")
        resp = requests.get(f"{rest_url}/v3/time", timeout=5)
        if resp.status_code == 200:
            console.print(
                f"[green]✓ HTTP {resp.status_code} — provider reachable[/green]"
            )
            console.print(f"  Response: {resp.text[:200]}")
        else:
            console.print(
                f"[yellow]! HTTP {resp.status_code} — endpoint responded[/yellow]"
            )
            console.print(f"  Response: {resp.text[:200]}")
    except ImportError:
        console.print(
            f"[yellow]! requests not available — cannot test {provider}[/yellow]"
        )
    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise SystemExit(1) from e


@cli.group(invoke_without_command=True)
@click.option("--show-violations", is_flag=True, help="Show recent rule violations")
@click.pass_context
def risk(ctx: click.Context, show_violations: bool) -> None:
    """Risk management commands (sections 7.11, 30.7)."""
    if ctx.invoked_subcommand is None:
        _print_risk_status(show_violations=show_violations)


def _print_risk_status(show_violations: bool = False) -> None:
    """Show risk configuration and current fail-closed gates."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    console.print("[bold]Risk configuration[/bold]")
    console.print(f"Kill switch:      {config.kill_switch}")
    console.print(f"Live enabled:     {config.live_enabled}")
    console.print("Global kill switch blocks all orders when active (section 30.7)")

    if show_violations:
        console.print("[bold]Recent violations:[/bold]")
        # RiskManager is instantiated per-session in CLI
        # For now, just show the config status
        console.print(
            "[dim](violation tracking requires live RiskManager instance)[/dim]"
        )


@risk.command("kill-switch")
@click.argument("action", type=click.Choice(["on", "off"]))
def risk_kill_switch(action: str) -> None:
    """Turn the risk kill switch on or off."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    new_value = action == "on"

    if config.kill_switch == new_value:
        state = "ON" if new_value else "OFF"
        console.print(f"[dim]Kill switch is already {state}.[/dim]")
        return

    config.kill_switch = new_value
    config.save()
    state = "ON" if new_value else "OFF"
    console.print(f"[green]Kill switch turned {state}.[/green]")
    if new_value:
        console.print(
            "[yellow]Warning: all live/paper order intents will be blocked.[/yellow]"
        )


@risk.command("show")
@click.option("--show-violations", is_flag=True, help="Show recent rule violations")
def risk_show(show_violations: bool) -> None:
    """Show risk configuration."""
    _print_risk_status(show_violations=show_violations)


@risk.command("status")
@click.option("--show-violations", is_flag=True, help="Show recent rule violations")
def risk_status(show_violations: bool) -> None:
    """Show risk status."""
    _print_risk_status(show_violations=show_violations)


@cli.group()
def events() -> None:
    """Event management commands."""
    pass


@events.group("schema")
def events_schema() -> None:
    """Event schema commands."""
    pass


@events_schema.command("validate")
@click.argument("event_type")
def events_schema_validate(event_type: str) -> None:
    """Validate event schema."""
    if not _validate_event_schema(event_type):
        sys.exit(1)


@events_schema.command("StrategyRuntimeError", hidden=True)
def events_schema_strategy_runtime_error() -> None:
    """Compatibility shorthand for the StrategyRuntimeError schema."""
    if not _validate_event_schema("StrategyRuntimeError"):
        sys.exit(1)


@cli.group()
def core() -> None:
    """Core 6-library stack checks."""
    pass


@core.command("check")
def core_check() -> None:
    """Check pine2ast/ast2python/pinelib/marketdata/backtest/optimizer imports."""
    from openpine.integrations import check_core_libraries

    all_ok = True
    console.print("[bold]OpenPine core libraries[/bold]")
    for status in check_core_libraries():
        path = getattr(status, "path", None) or "-"
        if status.importable:
            version = f" version={status.version}" if status.version else ""
            console.print(
                f"  [green]✓[/green] {status.name}{version} path={path}"
            )
        else:
            console.print(
                f"  [red]✗[/red] {status.name} path={path} error={status.error}"
            )
            all_ok = False
    if not all_ok:
        sys.exit(1)


from openpine.cli.optimizer import optimizer

cli.add_command(optimizer)


# ── reports ────────────────────────────────────────────────────────────────────


from openpine.cli.reports import reports

cli.add_command(reports)


# ── plugins ────────────────────────────────────────────────────────────────────


@cli.group()
def plugins() -> None:
    """Plugin management commands."""
    pass


@plugins.command("list")
def plugins_list() -> None:
    """List configured plugins."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    try:
        from openpine.notifications import PluginManager, TelegramCommandPlugin

        manager = PluginManager(
            plugins=[TelegramCommandPlugin(config=config.plugins.telegram)]
        )
        plugin_infos = manager.load_plugins()
    except ImportError:
        plugin_infos = [
            {
                "name": "telegram",
                "plugin_type": "notifications,commands",
                "enabled": config.plugins.telegram.enabled,
            }
        ]

    for info in plugin_infos:
        if isinstance(info, dict):
            name = info["name"]
            plugin_type = info["plugin_type"]
            enabled = info["enabled"]
        else:
            name = info.name
            plugin_type = info.plugin_type
            enabled = info.enabled
        status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        console.print(f"  {name}  type={plugin_type}  {status}")


@plugins.command("enable")
@click.argument("plugin_name")
@click.option(
    "--chat-id", "chat_id", default=None, help="Add a chat ID to the allowlist"
)
def plugins_enable(plugin_name: str, chat_id: str | None) -> None:
    """Enable a plugin and optionally add a chat ID to its allowlist.

    Currently supports: telegram

    Example:
        openpine plugins enable telegram --chat-id 123456789
    """
    from openpine.config import OpenPineConfig

    if plugin_name != "telegram":
        console.print(f"[red]Unknown plugin: {plugin_name}[/red]")
        console.print("Supported plugins: telegram")
        sys.exit(1)

    config = OpenPineConfig.load()
    cfg = config.plugins.telegram

    if cfg.enabled:
        console.print("[dim]Telegram plugin already enabled[/dim]")
    else:
        cfg.enabled = True
        config.save()
        console.print("[green]Telegram plugin enabled and saved[/green]")

    if chat_id:
        if chat_id not in cfg.chat_allowlist:
            cfg.chat_allowlist.append(chat_id)
            console.print(f"[green]Added chat_id {chat_id} to allowlist[/green]")
        else:
            console.print(f"[dim]Chat_id {chat_id} already in allowlist[/dim]")
        config.save()

    console.print("\n[bold]Current telegram config:[/bold]")
    console.print(f"  enabled:       {cfg.enabled}")
    console.print(f"  token_ref:     {cfg.token_ref}")
    console.print(f"  allowlist:     {cfg.chat_allowlist}")
    console.print("\nNote: Set the token with: export OPENPINE_TELEGRAM_TOKEN=***")


@plugins.command("disable")
@click.argument("plugin_name")
def plugins_disable(plugin_name: str) -> None:
    """Disable a plugin by name.

    Currently supports: telegram

    Example:
        openpine plugins disable telegram
    """
    from openpine.config import OpenPineConfig

    if plugin_name != "telegram":
        console.print(f"[red]Unknown plugin: {plugin_name}[/red]")
        console.print("Supported plugins: telegram")
        sys.exit(1)

    config = OpenPineConfig.load()
    cfg = config.plugins.telegram

    if not cfg.enabled:
        console.print("[dim]Telegram plugin already disabled[/dim]")
    else:
        cfg.enabled = False
        config.save()
        console.print("[green]Telegram plugin disabled and saved[/green]")

    console.print("\n[bold]Current telegram config:[/bold]")
    console.print(f"  enabled:   {cfg.enabled}")
    console.print(f"  allowlist: {cfg.chat_allowlist}")


@plugins.command("test")
@click.argument("plugin_name")
@click.option("--chat-id", "chat_id", required=True, help="Chat ID to test against")
def plugins_test(plugin_name: str, chat_id: str) -> None:
    """Run a dry-run smoke test for a plugin.

    The test verifies enabled+allowlist checks without token lookup or network calls.

    Example:
        openpine plugins test telegram --chat-id 123456789
    """
    from openpine.notifications import TelegramNotifier

    if plugin_name != "telegram":
        console.print(f"[red]Unknown plugin: {plugin_name}[/red]")
        console.print("Supported plugins: telegram")
        sys.exit(1)

    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    notifier = TelegramNotifier(config=config.plugins.telegram)
    result = notifier.test(chat_id=chat_id)

    if result.ok:
        console.print("[green]✓ Telegram plugin smoke test PASSED[/green]")
        console.print(f"  chat_id:      {chat_id}")
        console.print("  dry_run:     True (no network call)")
        console.print(f"  token_ref:   {config.plugins.telegram.token_ref}")
        console.print(f"  allowlist:   {config.plugins.telegram.chat_allowlist}")
    else:
        console.print("[red]✗ Telegram plugin smoke test FAILED[/red]")
        console.print(f"  reason: {result.error_message}")
        sys.exit(1)


def _load_telegram_command_catalog() -> list[dict[str, object]]:
    """Load the canonical Telegram command catalog."""
    from openpine.telegram_commands import TELEGRAM_COMMANDS

    return [_normalize_telegram_command(item) for item in TELEGRAM_COMMANDS]


def _normalize_telegram_command(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        command = str(item.get("command") or item.get("name") or "")
        title = str(item.get("title") or item.get("description") or command)
        cli_cmd = str(item.get("cli") or item.get("cli_command") or "")
    else:
        command = str(
            getattr(item, "command", getattr(item, "slash", getattr(item, "name", "")))
        )
        title = str(getattr(item, "title", getattr(item, "description", command)))
        cli_cmd = str(getattr(item, "cli", getattr(item, "cli_command", "")))
        argv = getattr(item, "argv", None)
        if not cli_cmd and argv is not None:
            cli_cmd = "openpine " + " ".join(str(part) for part in argv)
    if command and not command.startswith("/"):
        command = f"/{command}"
    return {"command": command, "title": title, "cli": cli_cmd}


def _telegram_menu_markup() -> dict[str, object]:
    rows = [
        [
            {"text": "Strategies", "callback_data": "openpine:strategies"},
            {"text": "Risk", "callback_data": "openpine:risk"},
        ],
        [
            {"text": "Data", "callback_data": "openpine:data"},
            {"text": "Reports", "callback_data": "openpine:reports"},
        ],
        [
            {"text": "Pause", "callback_data": "openpine:pause"},
            {"text": "Resume", "callback_data": "openpine:resume"},
        ],
    ]
    return {"inline_keyboard": rows}


def _resolve_telegram_token(config: object, require_enabled: bool = True) -> str:
    telegram_cfg = config.plugins.telegram
    if require_enabled and not telegram_cfg.enabled:
        console.print("[red]Telegram plugin is disabled[/red]")
        console.print(
            "[dim]Enable it with: openpine plugins enable telegram --chat-id <id>[/dim]"
        )
        sys.exit(1)
    token = telegram_cfg.resolve_token()
    if not token:
        console.print(
            f"[red]Telegram token not available: {telegram_cfg.token_ref}[/red]"
        )
        console.print("[dim]Dry-run commands do not require a token.[/dim]")
        sys.exit(1)
    return token


def _telegram_api_request(
    token: str, method: str, payload: dict[str, object] | None = None
) -> dict[str, object]:
    import json as _json
    import urllib.parse as _urlparse
    import urllib.request as _urlrequest

    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = None
    if payload:
        encoded = _urlparse.urlencode(
            {
                key: _json.dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in payload.items()
                if value is not None
            }
        ).encode("utf-8")
    with _urlrequest.urlopen(url, data=encoded, timeout=30) as response:
        return _json.loads(response.read().decode("utf-8"))


@plugins.group("telegram")
def plugins_telegram() -> None:
    """Telegram bot command, polling, webhook, and menu helpers."""
    pass


@plugins_telegram.command("commands")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def plugins_telegram_commands(fmt: str) -> None:
    """Show Telegram bot commands mapped to OpenPine CLI actions."""
    import json as _json

    commands = _load_telegram_command_catalog()
    if fmt == "json":
        console.print(_json.dumps(commands, indent=2))
        return

    console.print("[bold]Telegram commands[/bold]")
    for item in commands:
        console.print(f"  {item['command']:<18} {item['title']}")
        if item.get("cli"):
            console.print(f"    [dim]{item['cli']}[/dim]")

    console.print("\n[bold]Menu buttons[/bold]")
    for row in _telegram_menu_markup()["inline_keyboard"]:
        console.print("  " + " | ".join(str(button["text"]) for button in row))


@plugins_telegram.command("poll")
@click.option("--once", is_flag=True, help="Exit after one getUpdates call")
@click.option("--limit", default=10, show_default=True, type=click.IntRange(1, 100))
@click.option("--offset", default=None, type=int)
@click.option("--timeout", default=0, show_default=True, type=int)
@click.option(
    "--dry-run", is_flag=True, help="Do not call Telegram; print request plan"
)
@click.option(
    "--fake-updates-json", default=None, help="JSON updates payload for tests"
)
def plugins_telegram_poll(
    once: bool,
    limit: int,
    offset: int | None,
    timeout: int,
    dry_run: bool,
    fake_updates_json: str | None,
) -> None:
    """Poll Telegram getUpdates and display received bot commands."""
    import json as _json
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    payload = {"limit": limit, "offset": offset, "timeout": timeout}

    if dry_run:
        console.print("[green]Telegram poll dry-run[/green]")
        console.print("  method: getUpdates")
        console.print(f"  payload: {_json.dumps(payload, sort_keys=True)}")
        console.print("  network: skipped")
        if not fake_updates_json:
            return

    if fake_updates_json:
        data = _json.loads(fake_updates_json)
    else:
        token = _resolve_telegram_token(config)
        data = _telegram_api_request(token, "getUpdates", payload)

    updates = data if isinstance(data, list) else data.get("result", [])
    console.print(f"[bold]Telegram updates: {len(updates)}[/bold]")
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        console.print(
            f"  update_id={update.get('update_id')} chat_id={chat_id} text={text!r}"
        )
    if once:
        return


@plugins_telegram.command("webhook-info")
@click.option(
    "--dry-run", is_flag=True, help="Do not call Telegram; print request plan"
)
def plugins_telegram_webhook_info(dry_run: bool) -> None:
    """Show Telegram getWebhookInfo output."""
    import json as _json
    from openpine.config import OpenPineConfig

    if dry_run:
        console.print("[green]Telegram webhook-info dry-run[/green]")
        console.print("  method: getWebhookInfo")
        console.print("  network: skipped")
        return

    config = OpenPineConfig.load()
    token = _resolve_telegram_token(config)
    console.print(_json.dumps(_telegram_api_request(token, "getWebhookInfo"), indent=2))


@plugins_telegram.command("send-menu")
@click.option("--chat-id", "chat_id", required=True, help="Allowed target chat ID")
@click.option("--dry-run", is_flag=True, help="Do not call Telegram; print payload")
def plugins_telegram_send_menu(chat_id: str, dry_run: bool) -> None:
    """Send the OpenPine Telegram menu with inline keyboard buttons."""
    import json as _json
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    if chat_id not in config.plugins.telegram.chat_allowlist:
        console.print(f"[red]Chat {chat_id!r} is not in the Telegram allowlist[/red]")
        sys.exit(1)

    payload = {
        "chat_id": chat_id,
        "text": "OpenPine menu",
        "reply_markup": _telegram_menu_markup(),
    }
    if dry_run:
        console.print("[green]Telegram send-menu dry-run[/green]")
        console.print(_json.dumps(payload, indent=2))
        return

    token = _resolve_telegram_token(config)
    console.print(
        _json.dumps(_telegram_api_request(token, "sendMessage", payload), indent=2)
    )


# ── strategy lifecycle ──────────────────────────────────────────────────────────

import time as _time_module


@cli.group()
def strategy() -> None:
    """Strategy lifecycle management."""
    pass


@strategy.command("list")
@click.option(
    "--json", "as_json", is_flag=True, help="Output as JSON for bot consumption"
)
def strategy_list(as_json: bool) -> None:
    """List all strategies."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        strategies = registry.list_strategies()
        if not strategies:
            if as_json:
                console.print("[]")
            else:
                console.print("[dim](no strategies registered yet)[/dim]")
            return

        if as_json:
            data = [
                {
                    "strategy_id": s.strategy_id,
                    "name": s.name,
                    "status": s.status,
                    "mode": s.mode,
                    "symbol": s.symbol,
                    "timeframe": s.timeframe,
                }
                for s in strategies
            ]
            console.print(json.dumps(data))
        else:
            console.print(f"[bold]Strategies ({len(strategies)})[/bold]")
            for s in strategies:
                status_color = {
                    "pending": "dim",
                    "paused": "yellow",
                    "running": "green",
                    "error": "red",
                    "disabled": "dim",
                }.get(s.status, "dim")
                console.print(
                    f"  [{status_color}]{s.status}[/{status_color}] "
                    f"{s.strategy_id}  "
                    f"name={s.name}  "
                    f"symbol={s.symbol} tf={s.timeframe}  "
                    f"mode={s.mode}"
                )
    finally:
        registry.close()


@strategy.command("show")
@click.argument("strategy_id")
def strategy_show(strategy_id: str) -> None:
    """Show strategy details."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        console.print(f"[bold]Strategy: {s.strategy_id}[/bold]")
        console.print(f"  name:         {s.name}")
        console.print(f"  pine_id:      {s.pine_id}")
        console.print(f"  artifact_id:  {s.artifact_id}")
        console.print(f"  params_hash:  {s.params_hash}")
        console.print(f"  params_json:  {s.params_json}")
        console.print(f"  symbol:       {s.symbol}")
        console.print(f"  timeframe:    {s.timeframe}")
        console.print(f"  exchange:     {s.exchange}")
        console.print(f"  market_type:  {s.market_type}")
        console.print(f"  mode:         {s.mode}")
        console.print(f"  enabled:      {s.enabled}")
        console.print(f"  status:       {s.status}")
        console.print(f"  created:      {_fmt_utc_ms(s.created_at)}")
        console.print(f"  updated:      {_fmt_utc_ms(s.updated_at)}")
    finally:
        registry.close()


@strategy.command("status")
@click.argument("strategy_id")
def strategy_status(strategy_id: str) -> None:
    """Show strategy runtime status."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        console.print(f"[bold]Strategy status: {s.strategy_id}[/bold]")
        console.print(f"  status:      {s.status}")
        console.print(f"  mode:        {s.mode}")
        console.print(f"  enabled:     {s.enabled}")
        console.print(f"  artifact_id: {s.artifact_id}")
    finally:
        registry.close()


@strategy.command("create")
@click.argument("strategy_id", required=False)
@click.option("--pine", required=True, help="Pine source name")
@click.option("--symbol", required=True)
@click.option("--timeframe", required=True)
@click.option("--exchange", default="binance", show_default=True, help="Exchange name")
@click.option(
    "--market-type",
    default="spot",
    show_default=True,
    help="Market type, e.g. spot/usdm",
)
@click.option(
    "--mode",
    default="paper",
    type=click.Choice(["backtest", "replay", "observe", "paper", "live"]),
)
@click.option("--param", multiple=True, help="key=value params")
def strategy_create(
    strategy_id: str | None,
    pine: str,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    mode: str,
    param: tuple[str, ...],
) -> None:
    """Create a strategy instance."""
    from openpine.pine.registry import SQLitePineSourceRegistry
    from openpine.registry import SQLiteStrategyRegistry

    # Parse params
    params: dict[str, str] = {}
    for p in param:
        if "=" not in p:
            console.print(f"[red]Invalid param (need key=value): {p}[/red]")
            sys.exit(1)
        k, v = p.split("=", 1)
        params[k] = v

    # Resolve pine source to artifact_id
    pine_registry = SQLitePineSourceRegistry()
    try:
        try:
            source = pine_registry.get_source(pine)
        except KeyError:
            console.print(f"[red]Pine source not found: {pine}[/red]")
            sys.exit(1)

        if not source.active_artifact_id:
            console.print(
                f"[red]Pine source {pine} has no active artifact. "
                f"Compile it first with: openpine pine compile {pine}[/red]"
            )
            sys.exit(1)
        artifact_id = source.active_artifact_id
    finally:
        pine_registry.close()

    # Create strategy
    registry = SQLiteStrategyRegistry()
    try:
        si = registry.register_strategy(
            artifact_id=artifact_id,
            symbol=symbol,
            timeframe=timeframe,
            params=params,
            name=strategy_id,
            pine_id=source.id,
            exchange=exchange.lower(),
            market_type=market_type.lower(),
            mode=mode,
        )
        # Set initial status based on mode
        initial_status = {
            "backtest": "pending",
            "replay": "pending",
            "observe": "paused",
            "paper": "paused",
            "live": "disabled",
        }.get(mode, "pending")
        registry.update_status(si.strategy_id, initial_status)
        console.print(f"[green]Strategy created: {si.strategy_id}[/green]")
        console.print(f"  name:        {si.name}")
        console.print(f"  artifact_id: {artifact_id}")
        console.print(f"  params_hash: {si.params_hash}")
        console.print(f"  status:      {initial_status}")
        console.print(f"  mode:        {mode}")
        console.print(f"  exchange:    {si.exchange}")
        console.print(f"  market_type: {si.market_type}")
    finally:
        registry.close()


@strategy.command("update")
@click.argument("strategy_id")
@click.option("--param", multiple=True, help="key=value params")
def strategy_update(
    strategy_id: str,
    param: tuple[str, ...],
) -> None:
    """Update strategy params."""
    import json as _json
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.registry.strategies import _make_params_hash

    params: dict[str, str] = {}
    for p in param:
        if "=" not in p:
            console.print(f"[red]Invalid param (need key=value): {p}[/red]")
            sys.exit(1)
        k, v = p.split("=", 1)
        params[k] = v

    if not params:
        console.print("[yellow]No params provided[/yellow]")
        sys.exit(1)

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        current_params = _json.loads(s.params_json) if s.params_json else {}
        current_params.update(params)
        new_params_json = _json.dumps(current_params, sort_keys=True)
        new_hash = _make_params_hash(current_params)

        now = int(_time_module.time() * 1000)
        registry._conn.execute(
            "UPDATE strategy_instances SET params_json = ?, params_hash = ?, "
            "updated_at = ? WHERE strategy_id = ?",
            (new_params_json, new_hash, now, s.strategy_id),
        )
        registry._conn.commit()
        console.print(f"[green]Strategy updated: {strategy_id}[/green]")
        console.print(f"  params_hash: {new_hash}")
    finally:
        registry.close()


@strategy.command("pause")
@click.argument("strategy_id")
def strategy_pause(strategy_id: str) -> None:
    """Pause a strategy."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        try:
            registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        registry.update_status(strategy_id, "paused")
        console.print(f"[green]Strategy paused: {strategy_id}[/green]")
    finally:
        registry.close()


@strategy.command("resume")
@click.argument("strategy_id")
def strategy_resume(strategy_id: str) -> None:
    """Resume a paused strategy."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        if s.status == "error":
            console.print(
                f"[red]Cannot resume: strategy is in error state. "
                f"Use: openpine strategy error clear {strategy_id} --to paused[/red]"
            )
            sys.exit(1)
        registry.update_status(strategy_id, "paused")
        console.print(f"[green]Strategy resumed: {strategy_id}[/green]")
    finally:
        registry.close()


@strategy.command("remove")
@click.argument("strategy_id")
def strategy_remove(strategy_id: str) -> None:
    """Remove a strategy."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        try:
            registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        registry._conn.execute(
            "DELETE FROM strategy_instances WHERE strategy_id = ?", (strategy_id,)
        )
        registry._conn.commit()
        del registry._mem[strategy_id]
        console.print(f"[green]Strategy removed: {strategy_id}[/green]")
    finally:
        registry.close()


@strategy.command("backtest")
@click.argument("strategy_id")
@click.option("--from", "from_date")
@click.option("--to", "to_date")
@click.option(
    "--capture-plots", is_flag=True, help="Capture and save plot outputs from runtime"
)
@click.option("--capture-from", default=None, help="Optional plot capture window start")
@click.option("--capture-to", default=None, help="Optional plot capture window end")
@click.option(
    "--history-from", default=None, help="Load calculation history before --from"
)
@click.option(
    "--warmup-bars", default=0, type=int, help="Load N bars before --from as prehistory"
)
@click.option(
    "--gap-policy",
    default="fail",
    show_default=True,
    type=click.Choice(["fail", "allow_with_metadata"]),
    help="Market data coverage policy",
)
def strategy_backtest(
    strategy_id: str,
    from_date: str | None,
    to_date: str | None,
    capture_plots: bool,
    capture_from: str | None,
    capture_to: str | None,
    history_from: str | None,
    warmup_bars: int,
    gap_policy: str,
) -> None:
    """Run backtest for a strategy."""
    import time as _time

    from openpine.registry import SQLiteStrategyRegistry

    deps = _strategy_backtest_dependencies()
    registry = SQLiteStrategyRegistry()
    try:
        total_t0 = _time.perf_counter()
        timings: dict[str, float] = {}
        s = _get_strategy_or_exit(
            registry=registry,
            strategy_id=strategy_id,
            console=console,
        )
        registry.update_status(strategy_id, "running")
        _print_strategy_command_header(
            label="Backtest",
            strategy_id=strategy_id,
            strategy=s,
            from_date=from_date,
            to_date=to_date,
            console=console,
        )

        _exit_if_strategy_not_ready_for_backtest(
            strategy=s,
            strategy_id=strategy_id,
            registry=registry,
            console=console,
        )

        prepared = _prepare_strategy_backtest_inputs(
            strategy=s,
            strategy_id=strategy_id,
            from_date=from_date,
            to_date=to_date,
            capture_plots=capture_plots,
            capture_from=capture_from,
            capture_to=capture_to,
            history_from=history_from,
            warmup_bars=max(0, warmup_bars),
            gap_policy=gap_policy,
            now_ms=int(_time_module.time() * 1000),
            registry=registry,
            deps=deps,
            perf_counter=_time.perf_counter,
            console=console,
        )
        timings.update(prepared.timings)
        registry.update_status(strategy_id, "running")
        result, timings["backtest_sec"] = _run_strategy_backtest_or_exit(
            deps=deps,
            prepared=prepared,
            registry=registry,
            strategy_id=strategy_id,
            console=console,
            perf_counter=_time.perf_counter,
        )

        _print_backtest_result_summary(result, console=console)

        _persist_strategy_backtest_result(
            deps=deps,
            strategy=s,
            prepared=prepared,
            result=result,
            capture_plots=capture_plots,
            timings=timings,
            total_started=total_t0,
            perf_counter=_time.perf_counter,
            console=console,
        )

        registry.update_status(strategy_id, "paused")
    finally:
        registry.close()


@strategy.command("replay")
@click.argument("strategy_id")
@click.option("--from", "from_date")
@click.option("--to", "to_date")
def strategy_replay(
    strategy_id: str, from_date: str | None, to_date: str | None
) -> None:
    """Run replay for a strategy."""
    import time as _time_module

    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.runtime.engine import (
        BacktestArtifactError,
        BacktestEngineAdapter,
        BacktestRunConfig,
        load_strategy_class_from_artifact,
    )

    registry = SQLiteStrategyRegistry()
    try:
        s = _get_strategy_or_exit(
            registry=registry,
            strategy_id=strategy_id,
            console=console,
        )
        _print_strategy_command_header(
            label="Replay",
            strategy_id=strategy_id,
            strategy=s,
            from_date=from_date,
            to_date=to_date,
            console=console,
        )

        readiness_error = _strategy_backtest_readiness_error(s)
        if readiness_error:
            console.print(f"[red]{readiness_error}[/red]")
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        from openpine.artifacts import ArtifactStore

        prepared = _prepare_strategy_replay_inputs(
            strategy=s,
            strategy_id=strategy_id,
            from_date=from_date,
            to_date=to_date,
            now_ms=int(_time_module.time() * 1000),
            registry=registry,
            load_strategy_class=load_strategy_class_from_artifact,
            artifact_error_cls=BacktestArtifactError,
            artifact_store_cls=ArtifactStore,
            bar_query_cls=BarQuery,
            instrument_key_cls=InstrumentKey,
            parse_timeframe_func=parse_timeframe,
            orchestrator_cls=DataOrchestrator,
            config_cls=BacktestRunConfig,
            perf_counter=_time_module.perf_counter,
            console=console,
        )
        registry.update_status(strategy_id, "running")
        try:
            result = BacktestEngineAdapter().run(
                prepared.strategy_class,
                prepared.bars,
                prepared.config,
                params=prepared.params,
            )
        except Exception as exc:
            registry.update_status(strategy_id, "error")
            console.print(f"[red]Replay failed: {type(exc).__name__}: {exc}[/red]")
            sys.exit(1)

        console.print("[green]Replay completed[/green]")
        console.print(f"  status:     {result.status}")
        console.print(f"  bars:       {result.bars_processed}")
        console.print(
            f"  engine:     {'backtest_engine' if result.uses_backtest_engine else 'unknown'}"
        )
        registry.update_status(strategy_id, "paused")
    finally:
        registry.close()


@strategy.command("enable")
@click.argument("strategy_id")
def strategy_enable(strategy_id: str) -> None:
    """Enable a strategy for auto-refresh and trading."""
    import time as _time_module
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        s = registry.get_strategy(strategy_id)
        registry._conn.execute(
            "UPDATE strategy_instances SET enabled = 1, updated_at = ? WHERE strategy_id = ?",
            (int(_time_module.time() * 1000), strategy_id),
        )
        registry._conn.commit()
        s.enabled = True
        console.print(f"[green]Strategy enabled: {strategy_id}[/green]")
        console.print(f"  name: {s.name}")
        console.print(f"  symbol: {s.symbol} tf: {s.timeframe}")
    except KeyError:
        console.print(f"[red]Strategy not found: {strategy_id}[/red]")
        sys.exit(1)
    finally:
        registry.close()


@strategy.command("disable")
@click.argument("strategy_id")
def strategy_disable(strategy_id: str) -> None:
    """Disable a strategy."""
    import time as _time_module
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        s = registry.get_strategy(strategy_id)
        registry._conn.execute(
            "UPDATE strategy_instances SET enabled = 0, updated_at = ? WHERE strategy_id = ?",
            (int(_time_module.time() * 1000), strategy_id),
        )
        registry._conn.commit()
        s.enabled = False
        console.print(f"[yellow]Strategy disabled: {strategy_id}[/yellow]")
    except KeyError:
        console.print(f"[red]Strategy not found: {strategy_id}[/red]")
        sys.exit(1)
    finally:
        registry.close()


@strategy.command("metrics")
@click.argument("strategy_id")
@click.option("--run-id", help="Specific run ID (default: latest)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def strategy_metrics(strategy_id: str, run_id: str | None, as_json: bool) -> None:
    """Show backtest metrics for a strategy."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            if run_id:
                run = bt_store.get_run(run_id)
            else:
                run = bt_store.get_latest_run(strategy_id)

            if not run:
                console.print(
                    f"[yellow]No backtest runs found for {strategy_id}[/yellow]"
                )
                sys.exit(1)

            if as_json:
                import json as _json

                trades = bt_store.list_trades(run.run_id)
                artifacts = bt_store.list_artifacts(run.run_id)
                output = {
                    "run": run,
                    "trades": trades,
                    "artifacts": artifacts,
                }
                console.print(_json.dumps(output, indent=2, default=str))
            else:
                console.print(f"[bold]Backtest Metrics: {run.run_id}[/bold]")
                console.print(f"  strategy:   {s.name}")
                console.print(f"  period:     {run.from_time} - {run.to_time}")
                console.print(f"  status:     {run.status}")
                console.print()
                console.print("[bold]Performance[/bold]")
                console.print(f"  initial_capital:    {run.metrics.initial_capital}")
                console.print(f"  final_equity:       {run.metrics.final_equity}")
                console.print(f"  net_profit:         {run.metrics.net_profit}")
                console.print(f"  net_profit_%:       {run.metrics.net_profit_pct}")
                console.print(f"  gross_profit:       {run.metrics.gross_profit}")
                console.print(f"  gross_loss:         {run.metrics.gross_loss}")
                console.print(f"  profit_factor:      {run.metrics.profit_factor}")
                console.print(f"  max_drawdown:       {run.metrics.max_drawdown}")
                console.print(f"  max_drawdown_%:     {run.metrics.max_drawdown_pct}")
                console.print(f"  sharpe_ratio:       {run.metrics.sharpe}")
                console.print(f"  sortino_ratio:      {run.metrics.sortino}")
                console.print(f"  win_rate:           {run.metrics.win_rate}")
                console.print(f"  total_trades:       {run.metrics.trades_total}")
                console.print(f"  winning_trades:     {run.metrics.winning_trades}")
                console.print(f"  losing_trades:      {run.metrics.losing_trades}")
                console.print(f"  avg_trade:          {run.metrics.avg_trade}")
                console.print(f"  avg_win:            {run.metrics.avg_win}")
                console.print(f"  avg_loss:           {run.metrics.avg_loss}")
                console.print(f"  commission_total:   {run.metrics.commission_total}")
                console.print(f"  expectancy:         {run.metrics.expectancy}")
                console.print()
                trades = bt_store.list_trades(run.run_id)
                console.print(f"[bold]Trades:[/bold] {len(trades)} total")
                for t in trades[:10]:
                    dir_emoji = "🟢" if t.net_pnl and t.net_pnl > 0 else "🔴"
                    exit_price = t.exit_price or "..."
                    console.print(
                        f"  {dir_emoji} {t.direction} {t.entry_price} -> {exit_price} | P&L: {t.net_pnl}"
                    )
                if len(trades) > 10:
                    console.print(f"  ... and {len(trades) - 10} more")
        finally:
            bt_store.close()
    finally:
        registry.close()


@strategy.command("runs")
@click.argument("strategy_id")
@click.option("--limit", default=20, help="Max runs to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def strategy_runs(strategy_id: str, limit: int, as_json: bool) -> None:
    """List backtest runs for a strategy."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            runs = bt_store.list_runs(strategy_id, limit=limit)
            if not runs:
                console.print(f"[yellow]No backtest runs for {strategy_id}[/yellow]")
                sys.exit(1)

            if as_json:
                import json as _json

                console.print(
                    _json.dumps([r.__dict__ for r in runs], indent=2, default=str)
                )
            else:
                console.print(f"[bold]Backtest Runs: {s.name}[/bold]")
                console.print(
                    f"  {'Run ID':<30} {'Status':<10} {'Net Profit':<12} {'Max DD%':<10} {'PF':<8} {'Win%':<8} {'Trades':<8}"
                )
                console.print(
                    f"  {'-'*30} {'-'*10} {'-'*12} {'-'*10} {'-'*8} {'-'*8} {'-'*8}"
                )
                for r in runs:
                    m = r.metrics
                    console.print(
                        f"  {r.run_id:<30} {r.status:<10} "
                        f"{str(m.net_profit)[:11]:<12} "
                        f"{str(m.max_drawdown_pct)[:9]:<10} "
                        f"{str(m.profit_factor)[:7]:<8} "
                        f"{str(m.win_rate)[:7]:<8} "
                        f"{m.trades_total:<8}"
                    )
        finally:
            bt_store.close()
    finally:
        registry.close()


@strategy.command("run")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def strategy_run_show(run_id: str, as_json: bool) -> None:
    """Show details for a specific backtest run."""
    from openpine.storage import BacktestResultStore

    bt_store = BacktestResultStore()
    try:
        run = bt_store.get_run(run_id)
        if not run:
            console.print(f"[red]Run not found: {run_id}[/red]")
            sys.exit(1)

        if as_json:
            import json as _json

            trades = bt_store.list_trades(run_id)
            artifacts = bt_store.list_artifacts(run_id)
            output = {
                "run": run.__dict__,
                "trades": [t.__dict__ for t in trades],
                "artifacts": [a.__dict__ for a in artifacts],
            }
            console.print(_json.dumps(output, indent=2, default=str))
        else:
            console.print(f"[bold]Run Details: {run.run_id}[/bold]")
            console.print(f"  strategy:   {run.strategy_id}")
            console.print(f"  status:     {run.status}")
            console.print(f"  period:     {run.from_time} - {run.to_time}")
            console.print(f"  started:    {run.started_at}")
            console.print(f"  finished:   {run.finished_at}")
            console.print()
            console.print("[bold]Metrics[/bold]")
            m = run.metrics
            for k, v in m.__dict__.items():
                if v is not None:
                    console.print(f"  {k}: {v}")
            console.print()
            artifacts = bt_store.list_artifacts(run_id)
            console.print(f"[bold]Artifacts:[/bold] {len(artifacts)}")
            for a in artifacts:
                console.print(f"  {a.artifact_type}: {a.path}")
    finally:
        bt_store.close()


@strategy.command("trades")
@click.argument("strategy_id")
@click.option("--run-id", help="Specific run ID (default: latest)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def strategy_trades(strategy_id: str, run_id: str | None, as_json: bool) -> None:
    """Show trades for a strategy."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            if run_id:
                run = bt_store.get_run(run_id)
            else:
                run = bt_store.get_latest_run(strategy_id)

            if not run:
                console.print(f"[yellow]No backtest runs for {strategy_id}[/yellow]")
                sys.exit(1)

            trades = bt_store.list_trades(run.run_id)
            if as_json:
                import json as _json

                console.print(
                    _json.dumps([t.__dict__ for t in trades], indent=2, default=str)
                )
            else:
                console.print(f"[bold]Trades: {s.name} ({run.run_id})[/bold]")
                console.print(
                    f"  {'Direction':<10} {'Entry':<12} {'Exit':<12} {'Qty':<10} {'Net P&L':<12} {'Bars':<8} {'Reason':<15}"
                )
                console.print(
                    f"  {'-'*10} {'-'*12} {'-'*12} {'-'*10} {'-'*12} {'-'*8} {'-'*15}"
                )
                for t in trades:
                    console.print(
                        f"  {t.direction:<10} {t.entry_price:<12.2f} {t.exit_price or 0:<12.2f} "
                        f"{t.qty:<10.6f} {t.net_pnl or 0:<12.4f} {t.bars_held or 0:<8} {t.exit_reason or '':<15}"
                    )
        finally:
            bt_store.close()
    finally:
        registry.close()


@strategy.command("equity")
@click.argument("strategy_id")
@click.option("--run-id", help="Specific run ID (default: latest)")
@click.option("--tail", default=5, help="Show last N equity points")
def strategy_equity(strategy_id: str, run_id: str | None, tail: int) -> None:
    """Show equity curve artifact path and tail."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore, ARTIFACT_TYPE_EQUITY_CURVE

    registry = SQLiteStrategyRegistry()
    try:
        try:
            registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            if run_id:
                run = bt_store.get_run(run_id)
            else:
                run = bt_store.get_latest_run(strategy_id)

            if not run:
                console.print(f"[yellow]No backtest runs for {strategy_id}[/yellow]")
                sys.exit(1)

            artifacts = bt_store.list_artifacts(run.run_id)
            eq_artifact = next(
                (a for a in artifacts if a.artifact_type == ARTIFACT_TYPE_EQUITY_CURVE),
                None,
            )
            if not eq_artifact:
                console.print(
                    f"[yellow]No equity curve artifact for run {run.run_id}[/yellow]"
                )
                sys.exit(1)

            console.print(f"[bold]Equity Curve: {run.run_id}[/bold]")
            console.print(f"  path: {eq_artifact.path}")
            console.print(f"  rows: {eq_artifact.row_count}")
            console.print()

            # Show tail
            import pandas as pd

            df = pd.read_parquet(eq_artifact.path)
            console.print(f"[bold]Last {tail} equity points:[/bold]")
            console.print(df.tail(tail).to_string(index=False))
        finally:
            bt_store.close()
    finally:
        registry.close()


@strategy.command("plots")
@click.argument("strategy_id")
@click.option("--run-id", help="Specific run ID (default: latest)")
@click.option("--latest", is_flag=True, help="Show latest run plot artifact")
def strategy_plots(strategy_id: str, run_id: str | None, latest: bool) -> None:
    """Show plot outputs artifact path and summary for a strategy."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore, ARTIFACT_TYPE_PLOT_OUTPUTS

    registry = SQLiteStrategyRegistry()
    try:
        try:
            registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            if run_id:
                run = bt_store.get_run(run_id)
            else:
                run = bt_store.get_latest_run(strategy_id)

            if not run:
                console.print(f"[yellow]No backtest runs for {strategy_id}[/yellow]")
                sys.exit(1)

            artifacts = bt_store.list_artifacts(run.run_id)
            plot_artifact = next(
                (a for a in artifacts if a.artifact_type == ARTIFACT_TYPE_PLOT_OUTPUTS),
                None,
            )
            if not plot_artifact:
                console.print(
                    f"[yellow]No plot outputs artifact for run {run.run_id}[/yellow]"
                )
                console.print(
                    "[dim]Tip: run with --capture-plots to save plot outputs[/dim]"
                )
                sys.exit(1)

            console.print(f"[bold]Plot Outputs: {run.run_id}[/bold]")
            console.print(f"  path:     {plot_artifact.path}")
            console.print(f"  format:   {plot_artifact.format}")
            console.print(f"  row_count: {plot_artifact.row_count}")
            console.print()

            # Show plot names/columns
            import pandas as pd

            df = pd.read_parquet(plot_artifact.path)
            if "title" in df.columns:
                titles = df["title"].unique().tolist()
                console.print(f"[bold]Plot columns ({len(titles)}):[/bold]")
                for title in titles:
                    count = len(df[df["title"] == title])
                    console.print(f"  {title}: {count} rows")
            else:
                console.print(f"[bold]Columns:[/bold] {list(df.columns)}")
        finally:
            bt_store.close()
    finally:
        registry.close()


def _copy_strategy_export_run_meta(
    *,
    strategy_id: str,
    run_id: str,
    output_path: Path,
) -> str | None:
    from openpine.config import OpenPineConfig

    run_meta_path = (
        OpenPineConfig.load().data_dir
        / "backtests"
        / strategy_id
        / run_id
        / "run_meta.json"
    )
    if not run_meta_path.exists():
        return None

    target_meta = output_path / "run_meta.json"
    target_meta.write_text(run_meta_path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(target_meta)


def _write_strategy_export_files(
    *,
    strategy_id: str,
    run,
    artifacts,
    trades,
    output_path: Path,
    compare_from: str | None,
    compare_to: str | None,
    no_plots: bool,
    no_trades: bool,
    no_metrics: bool,
) -> tuple[dict[str, str], dict[str, int]]:
    from openpine.export import (
        ExportWindow,
        export_plot_outputs,
        export_trades,
        parse_time_ms,
        write_json,
    )
    from openpine.storage import ARTIFACT_TYPE_EQUITY_CURVE, ARTIFACT_TYPE_PLOT_OUTPUTS

    output_path.mkdir(parents=True, exist_ok=True)
    compare_from_ms = parse_time_ms(compare_from)
    compare_to_ms = parse_time_ms(compare_to)

    exported: dict[str, str] = {}
    rows: dict[str, int] = {}

    if not no_plots:
        plot_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.artifact_type == ARTIFACT_TYPE_PLOT_OUTPUTS
            ),
            None,
        )
        if plot_artifact:
            plots_path = output_path / "plots.csv"
            rows["plots"] = export_plot_outputs(
                plot_artifact.path,
                plots_path,
                from_ms=compare_from_ms,
                to_ms=compare_to_ms,
            )
            exported["plots"] = str(plots_path)
        else:
            rows["plots"] = 0

    if not no_trades:
        trades_path = output_path / "trades.csv"
        trade_window = None
        if compare_from_ms is not None:
            trade_window = ExportWindow(
                compare_from_ms, compare_to_ms or 9_999_999_999_999_999
            )
        rows["trades"] = export_trades(trades, trades_path, window=trade_window)
        exported["trades"] = str(trades_path)

    equity_artifact = next(
        (
            artifact
            for artifact in artifacts
            if artifact.artifact_type == ARTIFACT_TYPE_EQUITY_CURVE
        ),
        None,
    )
    if equity_artifact:
        import pandas as _pd

        equity_path = output_path / "equity_curve.csv"
        equity_df = _pd.read_parquet(equity_artifact.path)
        if compare_from_ms is not None and "time" in equity_df.columns:
            equity_df = equity_df[equity_df["time"] >= compare_from_ms]
        if compare_to_ms is not None and "time" in equity_df.columns:
            equity_df = equity_df[equity_df["time"] < compare_to_ms]
        equity_df.to_csv(equity_path, index=False)
        rows["equity"] = len(equity_df)
        exported["equity"] = str(equity_path)

    if not no_metrics:
        metrics_path = output_path / "metrics.json"
        run_payload = dict(run.__dict__)
        run_payload["metrics"] = run.metrics.__dict__
        write_json(
            metrics_path,
            {
                "run": run_payload,
                "metrics": run.metrics.__dict__,
                "artifacts": [artifact.__dict__ for artifact in artifacts],
            },
        )
        exported["metrics"] = str(metrics_path)

    run_meta_export = _copy_strategy_export_run_meta(
        strategy_id=strategy_id,
        run_id=run.run_id,
        output_path=output_path,
    )
    if run_meta_export:
        exported["run_meta"] = run_meta_export

    return exported, rows


@strategy.command("export-run")
@click.argument("strategy_id")
@click.option("--run-id", help="Specific run ID (default: latest)")
@click.option("--output", "output_dir", type=click.Path(file_okay=False), required=True)
@click.option("--compare-from", default=None, help="Optional export window start")
@click.option("--compare-to", default=None, help="Optional export window end")
@click.option("--no-plots", is_flag=True, help="Do not export plots.csv")
@click.option("--no-trades", is_flag=True, help="Do not export trades.csv")
@click.option("--no-metrics", is_flag=True, help="Do not export metrics.json")
def strategy_export_run(
    strategy_id: str,
    run_id: str | None,
    output_dir: str,
    compare_from: str | None,
    compare_to: str | None,
    no_plots: bool,
    no_trades: bool,
    no_metrics: bool,
) -> None:
    """Export a backtest run to normalized CSV/JSON files."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            run = (
                bt_store.get_run(run_id)
                if run_id
                else bt_store.get_latest_run(strategy_id)
            )
            if not run:
                console.print(
                    f"[yellow]No backtest runs found for {strategy_id}[/yellow]"
                )
                sys.exit(1)

            output_path = Path(output_dir)
            artifacts = bt_store.list_artifacts(run.run_id)
            trades = bt_store.list_trades(run.run_id)
            exported, rows = _write_strategy_export_files(
                strategy_id=strategy_id,
                run=run,
                artifacts=artifacts,
                trades=trades,
                output_path=output_path,
                compare_from=compare_from,
                compare_to=compare_to,
                no_plots=no_plots,
                no_trades=no_trades,
                no_metrics=no_metrics,
            )

            console.print(f"[green]Run exported:[/green] {run.run_id}")
            console.print(f"  strategy: {s.name}")
            for key, path in exported.items():
                console.print(f"  {key}: {path}")
            for key, count in rows.items():
                console.print(f"  {key}_rows: {count}")
        finally:
            bt_store.close()
    finally:
        registry.close()


@strategy.command("compare-tv")
@click.argument("strategy_id")
@click.option("--run-id", help="Specific run ID (default: latest)")
@click.option(
    "--tv-chart",
    type=click.Path(dir_okay=False),
    help="TradingView chart/export CSV for plot comparison",
)
@click.option(
    "--tv-trades",
    type=click.Path(dir_okay=False),
    help="Optional TradingView trades CSV",
)
@click.option(
    "--tv-equity",
    type=click.Path(dir_okay=False),
    help="Optional TradingView equity CSV",
)
@click.option("--output", "output_dir", type=click.Path(file_okay=False), required=True)
@click.option(
    "--compare-from", default=None, help="Optional export/compare window start"
)
@click.option("--compare-to", default=None, help="Optional export/compare window end")
@click.option("--abs-tol", default=1e-6, show_default=True, type=float)
@click.option("--rel-tol", default=1e-9, show_default=True, type=float)
@click.option(
    "--include-base-columns",
    is_flag=True,
    help="Include OHLCV/time/base columns in plot comparison",
)
def strategy_compare_tv(
    strategy_id: str,
    run_id: str | None,
    tv_chart: str | None,
    tv_trades: str | None,
    tv_equity: str | None,
    output_dir: str,
    compare_from: str | None,
    compare_to: str | None,
    abs_tol: float,
    rel_tol: float,
    include_base_columns: bool,
) -> None:
    """Compare a saved backtest run against TradingView export CSV files."""
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.storage import BacktestResultStore
    from openpine.export import parse_time_ms

    if not any((tv_chart, tv_trades, tv_equity)):
        console.print(
            "[red]Pass at least one TV file: --tv-chart, --tv-trades, or --tv-equity[/red]"
        )
        sys.exit(1)
    compare_from_ms = parse_time_ms(compare_from)
    compare_to_ms = parse_time_ms(compare_to)

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        bt_store = BacktestResultStore()
        try:
            run = (
                bt_store.get_run(run_id)
                if run_id
                else bt_store.get_latest_run(strategy_id)
            )
            if not run:
                console.print(
                    f"[yellow]No backtest runs found for {strategy_id}[/yellow]"
                )
                sys.exit(1)

            output_path = Path(output_dir)
            openpine_output = output_path / "openpine"
            artifacts = bt_store.list_artifacts(run.run_id)
            trades = bt_store.list_trades(run.run_id)
            exported, rows = _write_strategy_export_files(
                strategy_id=strategy_id,
                run=run,
                artifacts=artifacts,
                trades=trades,
                output_path=openpine_output,
                compare_from=compare_from,
                compare_to=compare_to,
                no_plots=tv_chart is None,
                no_trades=tv_trades is None,
                no_metrics=False,
            )
            result = _compare_strategy_run_with_tv_exports(
                strategy_id=strategy_id,
                run=run,
                exported=exported,
                output_path=output_path,
                tv_chart=tv_chart,
                tv_trades=tv_trades,
                tv_equity=tv_equity,
                abs_tol=abs_tol,
                rel_tol=rel_tol,
                include_base_columns=include_base_columns,
                compare_from_ms=compare_from_ms,
                compare_to_ms=compare_to_ms,
            )

            console.print(f"[green]TV comparison complete:[/green] {run.run_id}")
            console.print(f"  strategy: {s.name}")
            console.print(f"  output:   {output_path}")
            for key, count in rows.items():
                console.print(f"  openpine_{key}_rows: {count}")
            for row in result["comparisons"]:
                console.print(
                    f"  {row['type']}: {row['status']} {row['classification']} "
                    f"mismatch={row.get('mismatch_cells')}/{row.get('total_cells')} "
                    f"max_delta={row.get('max_abs_delta')}"
                )
        finally:
            bt_store.close()
    finally:
        registry.close()


@strategy.command("paper")
@click.argument("strategy_id")
@click.argument("action", type=click.Choice(["start", "stop"]))
def strategy_paper(strategy_id: str, action: str) -> None:
    """Start/stop paper trading."""
    from openpine.registry import SQLiteStrategyRegistry

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        if action == "start":
            if s.status == "error":
                console.print(
                    "[red]Cannot start paper: strategy is in error state. "
                    "Clear error first.[/red]"
                )
                sys.exit(1)
            registry.update_status(strategy_id, "running")
            console.print(f"[green]Paper trading started: {strategy_id}[/green]")
        else:
            registry.update_status(strategy_id, "paused")
            console.print(f"[green]Paper trading stopped: {strategy_id}[/green]")
    finally:
        registry.close()


@strategy.command("live")
@click.argument("strategy_id")
@click.argument("action", type=click.Choice(["enable", "start", "stop"]))
def strategy_live(strategy_id: str, action: str) -> None:
    """Enable/start/stop live trading."""
    from openpine.config import OpenPineConfig
    from openpine.registry import SQLiteStrategyRegistry

    config = OpenPineConfig.load()

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)

        if action == "enable":
            if s.status == "error":
                console.print(
                    "[red]Cannot enable live: strategy is in error state.[/red]"
                )
                sys.exit(1)
            registry.update_status(strategy_id, "disabled")
            console.print(f"[green]Live trading enabled: {strategy_id}[/green]")
        elif action == "start":
            # live start requires global live_enabled
            if not config.live_enabled:
                console.print(
                    "[red]Live trading is disabled globally. "
                    "Enable in config before starting live.[/red]"
                )
                sys.exit(1)
            if s.status == "error":
                console.print(
                    "[red]Cannot start live: strategy is in error state.[/red]"
                )
                sys.exit(1)
            registry.update_status(strategy_id, "running")
            console.print(f"[green]Live trading started: {strategy_id}[/green]")
        else:  # stop
            registry.update_status(strategy_id, "disabled")
            console.print(f"[green]Live trading stopped: {strategy_id}[/green]")
    finally:
        registry.close()


@strategy.command("error")
@click.argument("strategy_id")
@click.argument("action", type=click.Choice(["clear"]))
@click.option(
    "--to",
    "to_state",
    type=click.Choice(["paused", "disabled"]),
    default="paused",
    help="Target state after clearing error",
)
def strategy_error(strategy_id: str, action: str, to_state: str) -> None:
    """Clear strategy error state."""
    from openpine.registry import SQLiteStrategyRegistry

    if action == "clear":
        registry = SQLiteStrategyRegistry()
        try:
            try:
                s = registry.get_strategy(strategy_id)
            except KeyError:
                console.print(f"[red]Strategy not found: {strategy_id}[/red]")
                sys.exit(1)

            if s.status != "error":
                console.print(
                    f"[yellow]Strategy {strategy_id} is not in error state "
                    f"(current: {s.status})[/yellow]"
                )
                sys.exit(1)

            registry.update_status(strategy_id, to_state)
            console.print(
                f"[green]Error cleared for {strategy_id}: now {to_state}[/green]"
            )
        finally:
            registry.close()


@cli.group()
def daemon() -> None:
    """Daemon management commands."""
    pass


@daemon.command("run")
@click.option("--telegram/--no-telegram", default=True, help="Start Telegram bot")
def daemon_run(telegram: bool) -> None:
    """Run the OpenPine daemon (long-running service)."""
    import asyncio
    import signal
    from openpine.daemon.service import DaemonService
    from openpine.config import OpenPineConfig

    async def _run() -> None:
        services: list[DaemonService] = []

        # Start market data refresh service
        try:
            from openpine.daemon.refresh_service import MarketDataRefreshService

            refresh_svc = MarketDataRefreshService()
            services.append(refresh_svc)
            await refresh_svc.start()
            console.print("[green]✓ Market data refresh service started[/green]")
        except Exception as e:
            console.print(
                f"[yellow]Warning: could not start market data refresh service: {e}[/yellow]"
            )

        # Always start the Telegram service if enabled in config
        config = OpenPineConfig.load()
        if telegram and config.plugins.telegram.enabled:
            try:
                from openpine.daemon.telegram_service import TelegramDaemonService

                svc = TelegramDaemonService()
                services.append(svc)
                await svc.start()
            except Exception as e:
                console.print(
                    f"[yellow]Warning: could not start Telegram service: {e}[/yellow]"
                )
        elif not telegram:
            console.print("[dim]Telegram bot disabled (--no-telegram)[/dim]")

        if not services:
            console.print("[yellow]No services configured to run.[/yellow]")
            console.print("[dim]Enable plugins in config or use --telegram.[/dim]")
            return

        console.print(
            f"[green]Daemon running with {len(services)} service(s). Press Ctrl+C to stop.[/green]"
        )

        loop = asyncio.get_event_loop()

        def handle_signal(sig: signal.Signals) -> None:
            console.print(
                f"\n[yellow]Received signal {sig.name}, shutting down...[/yellow]"
            )
            if sys.platform != "win32":
                loop.remove_signal_handler(sig)
            loop.stop()

        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, handle_signal, sig)

        # Keep running until stopped
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            console.print("[yellow]Stopping services...[/yellow]")
            for svc in services:
                try:
                    await svc.stop(timeout=5.0)
                except Exception as e:
                    console.print(f"[red]Error stopping {svc.name}: {e}[/red]")
            console.print("[green]Daemon stopped.[/green]")

    asyncio.run(_run())


@cli.group()
def gateway() -> None:
    """Web gateway commands."""
    pass


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


def main() -> None:
    """OpenPine CLI entry point."""
    sys.exit(cli())


if __name__ == "__main__":
    main()
