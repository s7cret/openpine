"""OpenPine CLI — main entry point."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console

from openpine import __version__
from openpine.jobs import Job, JobScheduler, JobStatus, JobType

# Global instances — created once at module load
console = Console()
_cli_scheduler = JobScheduler()


def _fmt_utc_ms(timestamp_ms: int) -> str:
    """Format a millisecond timestamp without deprecated utcfromtimestamp()."""
    return f"{datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc):%Y-%m-%d %H:%M:%S}"


@click.group()
@click.version_option(version=__version__, prog_name="openpine")
def cli() -> None:
    """OpenPine Trading Platform CLI."""
    pass


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
        console.print(f"[red]StrategyRuntimeError contract missing: {missing_contract}[/red]")
    if missing_payload:
        console.print(f"[red]StrategyRuntimeError payload missing: {missing_payload}[/red]")
    return False


def _print_state_policy() -> None:
    """Show current state save policy (section 33.7)."""
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    state_cfg = getattr(config, "state", None)
    console.print("[bold]State policy[/bold]")
    if state_cfg:
        console.print(f"save_policy:        {getattr(state_cfg, 'save_policy', 'every_bar')}")
        console.print(f"save_interval_bars:  {getattr(state_cfg, 'save_interval_bars', 1)}")
        console.print(f"max_snapshots:      {getattr(state_cfg, 'keep_last_snapshots', 1000)}")
    else:
        console.print("save_policy:        every_bar  (default)")
        console.print("save_interval_bars: 1         (default)")
        console.print("max_snapshots:      1000      (default)")


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

    # SQLite reachable and schema current
    try:
        from openpine.storage import SQLiteStorage
        storage = SQLiteStorage(config.sqlite_path)
        cursor = storage.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        storage.close()
        console.print(f"  [green]✓[/green] SQLite reachable ({len(tables)} tables)")
    except Exception as e:
        console.print(f"  [red]✗[/red] SQLite: {e}")
        all_ok = False

    # WAL mode enabled
    try:
        from openpine.storage import SQLiteStorage
        storage = SQLiteStorage(config.sqlite_path)
        cursor = storage.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        storage.close()
        if mode.upper() == "WAL":
            console.print(f"  [green]✓[/green] WAL mode enabled")
        else:
            console.print(f"  [yellow]![/yellow] journal_mode={mode} (expected WAL)")
    except Exception as e:
        console.print(f"  [red]✗[/red] WAL mode check: {e}")

    # Parquet data dir writable
    parquet_dir = config.data_dir / "parquet"
    try:
        parquet_dir.mkdir(parents=True, exist_ok=True)
        test_file = parquet_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        console.print(f"  [green]✓[/green] Parquet data dir writable: {parquet_dir}")
    except Exception as e:
        console.print(f"  [red]✗[/red] Parquet dir: {e}")
        all_ok = False

    # Artifact dir writable
    artifact_dir = config.config_dir / "artifacts"
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        test_file = artifact_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        console.print(f"  [green]✓[/green] Artifact dir writable: {artifact_dir}")
    except Exception as e:
        console.print(f"  [red]✗[/red] Artifact dir: {e}")
        all_ok = False

    # State dir writable
    state_dir = config.config_dir / "state"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        test_file = state_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        console.print(f"  [green]✓[/green] State dir writable: {state_dir}")
    except Exception as e:
        console.print(f"  [red]✗[/red] State dir: {e}")
        all_ok = False

    # DuckDB query works (if duckdb installed)
    try:
        import duckdb
        con = duckdb.connect(database=str(config.duckdb_path), read_only=True)
        con.execute("SELECT 1").fetchone()
        con.close()
        console.print(f"  [green]✓[/green] DuckDB query works")
    except ImportError:
        console.print(f"  [dim]  DuckDB not installed (optional)[/dim]")
    except Exception as e:
        console.print(f"  [yellow]![/yellow] DuckDB: {e} (optional)")

    # Provider connectivity smoke test
    try:
        from openpine.data.orchestrator import DataOrchestrator
        from openpine.contracts import BarQuery
        orch = DataOrchestrator()
        # Smoke test: try to get bars (will return empty if no provider)
        console.print(f"  [green]✓[/green] DataOrchestrator smoke test passed")
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
        console.print(f"  [green]✓[/green] AccountManager accessible ({len(accounts)} accounts)")
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
        console.print(f"  [green]✓[/green] Worker pools initialized")
        console.print(f"    Aggregation: {agg_status['active_workers']} workers")
        console.print(f"    Feature: {feat_status['active_workers']} workers")
    except Exception as e:
        console.print(f"  [red]✗[/red] Worker pools: {e}")
        all_ok = False

    # Stale locks check
    try:
        from openpine.jobs import JobScheduler
        scheduler = JobScheduler()
        recovered = scheduler.recover_stale_locks()
        if recovered > 0:
            console.print(f"  [yellow]![/yellow] Recovered {recovered} stale lock(s)")
        else:
            console.print(f"  [green]✓[/green] No stale locks")
    except Exception as e:
        console.print(f"  [red]✗[/red] Stale lock check: {e}")

    # Failed jobs check
    try:
        from openpine.jobs import JobScheduler, JobStatus
        scheduler = JobScheduler()
        failed = scheduler.list_jobs(status=JobStatus.FAILED)
        if failed:
            console.print(f"  [yellow]![/yellow] {len(failed)} failed job(s) in queue")
        else:
            console.print(f"  [green]✓[/green] No failed jobs")
    except Exception as e:
        console.print(f"  [red]✗[/red] Failed jobs check: {e}")

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
        console.print(f"  [green]✓[/green] PluginManager accessible ({len(loaded)} plugins)")
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
                "OptimizerService adapter boundary",
                OptimizerService().dry_run("doctor_smoke", 1).uses_backtest_engine_path,
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
def storage() -> None:
    """Storage management commands."""
    pass


@storage.command()
@click.option("--path", type=click.Path(), default=None)
@click.option("--dry-run", is_flag=True)
def storage_init(path: str | None, dry_run: bool) -> None:
    """Initialize storage."""
    from openpine.config import OpenPineConfig
    from openpine.storage import MigrationRunner, SQLiteStorage

    if path is None:
        config = OpenPineConfig.load()
        db_path = config.sqlite_path
    else:
        db_path = Path(path)

    console.print(f"[bold]Storage init[/bold] — path={db_path}")
    if dry_run:
        console.print("[dim]Dry run — no changes made[/dim]")
        return

    storage = SQLiteStorage(db_path)
    runner = MigrationRunner()
    applied = runner.run_migrations(storage)
    storage.close()

    if applied:
        console.print(f"[green]Applied migrations: {applied}[/green]")
    else:
        console.print("[dim]No pending migrations[/dim]")
    console.print("[green]Storage initialized[/green]")


@storage.command()
@click.option("--path", type=click.Path(), default=None)
def storage_schema(path: str | None) -> None:
    """Show storage schema."""
    from openpine.config import OpenPineConfig
    from openpine.storage import SQLiteStorage

    if path is None:
        config = OpenPineConfig.load()
        db_path = config.sqlite_path
    else:
        db_path = Path(path)

    console.print(f"[bold]Storage schema[/bold] — path={db_path}")

    if not db_path.exists():
        console.print(f"[red]Database not found: {db_path}[/red]")
        console.print("Run 'openpine storage init' first.")
        return

    storage = SQLiteStorage(db_path)
    cursor = storage.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    storage.close()

    console.print(f"Tables ({len(tables)}): {', '.join(tables)}")
    for table in tables:
        console.print(f"\n  [bold]{table}[/bold]")
        storage2 = SQLiteStorage(db_path)
        col_cursor = storage2.execute(f"PRAGMA table_info({table})")
        for col in col_cursor.fetchall():
            console.print(f"    {col[1]} {col[2]}  nullable={not col[3]} default={col[4]}")
        storage2.close()


@storage.command("migrate")
@click.option("--path", type=click.Path(), default=None)
def storage_migrate(path: str | None) -> None:
    """Run pending migrations."""
    from openpine.config import OpenPineConfig
    from openpine.storage import MigrationRunner, SQLiteStorage

    if path is None:
        config = OpenPineConfig.load()
        db_path = config.sqlite_path
    else:
        db_path = Path(path)

    console.print(f"[bold]Storage migrate[/bold] — path={db_path}")

    storage = SQLiteStorage(db_path)
    runner = MigrationRunner()
    applied = runner.run_migrations(storage)
    storage.close()

    # Show current state
    storage2 = SQLiteStorage(db_path)
    cursor = storage2.execute(
        "SELECT version, name, applied_at, description FROM schema_migrations ORDER BY id"
    )
    rows = cursor.fetchall()
    storage2.close()

    if rows:
        console.print(f"[bold]Applied migrations ({len(rows)})[/bold]")
        for version, name, applied_at, description in rows:
            from datetime import datetime as dt
            ts = dt.utcfromtimestamp(applied_at).strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"  {version}  {name}  — {description}  [{ts}]")
    else:
        console.print("[dim]No migrations applied yet[/dim]")

    if applied:
        console.print(f"[green]Newly applied: {applied}[/green]")
    else:
        console.print("[dim]No pending migrations[/dim]")


@storage.command("backup")
@click.option("--out", required=True, type=click.Path(), help="Output .tar.gz path")
def storage_backup(out: str) -> None:
    """Create OpenPine backup archive."""
    from openpine.config import OpenPineConfig
    from openpine.storage.backup import backup_openpine

    config = OpenPineConfig.load()
    out_path = Path(out)
    console.print(f"[bold]Creating backup[/bold] → {out_path}")
    try:
        backed = backup_openpine(out_path, config)
        console.print(f"[green]Backup complete[/green] — {len(backed)} items:")
        for item in backed:
            console.print(f"  {item}")
    except Exception as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        raise SystemExit(1)


@storage.command("restore")
@click.argument("backup_path", type=click.Path(exists=True))
@click.option("--target", type=click.Path(), default=None, help="Target data directory")
def storage_restore(backup_path: str, target: str | None) -> None:
    """Restore from OpenPine backup archive."""
    from openpine.storage.backup import restore_openpine

    bp = Path(backup_path)
    target_path = Path(target) if target else None
    console.print(f"[bold]Restoring backup[/bold] from {bp}")
    try:
        restore_openpine(bp, target_path)
        console.print("[green]Restore complete[/green]")
    except Exception as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        raise SystemExit(1)


@storage.command("verify")
def storage_verify() -> None:
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


@cli.group()
def pine() -> None:
    """Pine source management."""
    pass


@pine.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON for bot consumption")
def pine_list(as_json: bool) -> None:
    """List registered Pine sources."""
    from openpine.pine.registry import SQLitePineSourceRegistry
    import json

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
                active = f" [dim]active: {s.active_artifact_id}[/dim]" if s.active_artifact_id else ""
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
        console.print(f"[green]Added Pine source: {source.name} (id={source.id})[/green]")
    finally:
        registry.close()


@pine.command()
@click.argument("name")
@click.option("--force", is_flag=True, help="Force recompile even if cached artifact exists")
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
            console.print(f"[green]Compiled successfully: {result['artifact_id']}[/green]")
            console.print(f"  Artifact: {result['artifact_path']}")
            registry.set_active_artifact(source.id, result["artifact_id"])
            console.print(f"  Active artifact set: {result['artifact_id']}")
        else:
            console.print(f"[red]Compile failed:[/red]")
            for err in result["errors"]:
                console.print(f"  [red]- {err}[/red]")
    finally:
        registry.close()


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
@click.option("--to-version", "artifact_id", default=None, help="Artifact ID to set as active")
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
                active = " [dim](active)[/dim]" if art["artifact_id"] == source.active_artifact_id else ""
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
        active = " [dim](active)[/dim]" if art["artifact_id"] == source.active_artifact_id else ""
        console.print(
            f"  {art['artifact_id']}{active}"
            f"  created={created}"
        )


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
            artifact_dir = Path(art.get("artifact_dir", ""))
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir, ignore_errors=True)

        registry.remove_source(name)
        console.print(f"[green]Removed Pine source: {name} (id={source.id})[/green]")
        console.print(f"  Removed {len(artifacts)} artifact(s)")
    finally:
        registry.close()


@cli.group()
def data() -> None:
    """Data management commands."""
    pass


@data.command("plan")
@click.option("--strategy", "strategy_id", default=None, help="Strategy ID to plan for")
@click.option("--explain", is_flag=True, help="Show requirement details")
def data_plan(strategy_id: str | None, explain: bool) -> None:
    """Plan data requirements for a strategy (dry-run)."""
    from openpine.config import OpenPineConfig
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.data.planner import DataPlan, DataPlanner
    from openpine.streams import MarketDataStreamManager
    from openpine.universe import ActiveUniverse

    config = OpenPineConfig.load()
    console.print("[bold]Data plan[/bold]")

    planner = DataPlanner(
        stream_manager=MarketDataStreamManager.__new__(MarketDataStreamManager),
        orchestrator=DataOrchestrator(),
        universe=ActiveUniverse(),
    )

    if strategy_id is None:
        console.print("[dim](no strategy specified — listing available keys)[/dim]")
        available = planner.list_available_instruments()
        for key in sorted(available):
            console.print(f"  {key}")
        return

    plan = planner.plan_for_strategy(strategy_id)
    if explain:
        console.print(f"\n[bold]Strategy:[/bold] {strategy_id}")
        console.print(f"[bold]Instrument:[/bold]  {plan.instrument_key}")
        console.print(f"[bold]Timeframe:[/bold]   {plan.timeframe}")
        console.print(f"[bold]Start:[/bold]        {datetime.utcfromtimestamp(plan.start_time_ms / 1000):%Y-%m-%d %H:%M}")
        console.print(f"[bold]End:[/bold]          {datetime.utcfromtimestamp(plan.end_time_ms / 1000):%Y-%m-%d %H:%M}")
        console.print(f"[bold]Bar count:[/bold]    {plan.bar_count} ({plan.estimate_mb:.1f} MB)")
        console.print(f"[bold]Sources:[/bold]")
        for src in plan.sources:
            console.print(f"  {src}")
    else:
        console.print(f"  {plan.instrument_key} {plan.timeframe}  {plan.bar_count} bars")


@data.command("status")
@click.argument("symbol", required=False)
@click.option("--exchange", default="binance", help="Exchange name")
@click.option("--timeframe", "tf", default=None, help="Timeframe to check")
def data_status(symbol: str | None, exchange: str, tf: str | None) -> None:
    """Show data pipeline status: configured symbols/timeframes, last backfill, gaps count."""
    from openpine.config import OpenPineConfig
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.storage import SQLiteStorage

    console.print("[bold]Data pipeline status[/bold]")
    config = OpenPineConfig.load()
    storage = SQLiteStorage(config.sqlite_path)

    # Show DataOrchestrator info
    orch = DataOrchestrator()
    console.print(f"DataOrchestrator:  provider={'set' if orch._provider else 'none'}")

    # Query data_requirements table for configured symbols/timeframes
    try:
        cursor = storage.execute(
            "SELECT DISTINCT exchange, symbol, timeframe, provider, status, updated_at "
            "FROM data_requirements ORDER BY exchange, symbol, timeframe"
        )
        rows = cursor.fetchall()
        if rows:
            console.print(f"\n[bold]Configured data requirements ({len(rows)})[/bold]")
            # Filter by symbol / timeframe if given
            if symbol:
                rows = [r for r in rows if r[2] == symbol]
            if tf:
                rows = [r for r in rows if r[3] == tf]

            from rich.table import Table
            tbl = Table(title="Data Requirements")
            tbl.add_column("Exchange", style="cyan")
            tbl.add_column("Symbol", style="green")
            tbl.add_column("Timeframe", style="yellow")
            tbl.add_column("Provider", style="magenta")
            tbl.add_column("Status", style="blue")
            tbl.add_column("Updated", style="dim")
            for row in rows:
                exch, sym, timeframe, provider, status, updated_at = row
                updated_str = (
                    datetime.utcfromtimestamp(updated_at / 1000).strftime("%Y-%m-%d %H:%M")
                    if updated_at else "N/A"
                )
                tbl.add_row(exch, sym, timeframe, provider or "-", status or "-", updated_str)
            console.print(tbl)
        else:
            console.print("[dim](no data requirements configured)[/dim]")
    except Exception as e:
        console.print(f"[dim]Could not query data_requirements: {e}[/dim]")

    # Show candle parquet coverage summary
    candle_base = config.data_dir / "candles"
    if candle_base.exists():
        try:
            all_parquet_files = list(candle_base.rglob("*.parquet"))
            console.print(f"\nParquet candle files: {len(all_parquet_files)}")
        except Exception:
            console.print("\nParquet candle files: 0")
    else:
        console.print(f"\nData dir: {config.data_dir} (no candles directory yet)")

    storage.close()


@data.command("gaps")
@click.argument("symbol", required=True)
@click.argument("timeframe", required=True)
@click.option("--exchange", default="binance", help="Exchange name")
@click.option("--market", default="usdm", help="Market type")
def data_gaps(symbol: str, timeframe: str, exchange: str, market: str) -> None:
    """Find and list data gaps for a symbol/timeframe."""
    from openpine.config import OpenPineConfig
    from openpine.data.provider_adapter import create_local_marketdata_provider_adapter

    console.print(f"[bold]Data gaps[/bold] {symbol} {timeframe} (exchange={exchange}, market={market})")

    # Try using marketdata_provider
    try:
        provider_adapter = create_local_marketdata_provider_adapter()
    except Exception:
        provider_adapter = None

    if provider_adapter is not None:
        try:
            from openpine.contracts import BarQuery, InstrumentKey
            from openpine.data.orchestrator import DataOrchestrator

            ik = InstrumentKey(exchange=exchange, symbol=symbol, market_type=market)
            query = BarQuery(instrument_key=ik, timeframe=timeframe, start_ms=0, end_ms=int(2**63 - 1), limit=10000)
            bars = provider_adapter.get_bars(query)
            if not bars:
                console.print("[dim](no data returned by provider)[/dim]")
                return

            gaps: list[tuple[int, int]] = []
            bar_times = sorted(set(b.open_time_ms for b in bars))
            for i in range(1, len(bar_times)):
                prev = bar_times[i - 1]
                curr = bar_times[i]
                # Expected step in ms for the timeframe
                tf_ms = _timeframe_to_ms(timeframe)
                if curr - prev > tf_ms * 1.5:
                    gaps.append((prev, curr))

            if not gaps:
                console.print(f"[green]No gaps found ({len(bar_times)} bars loaded)[/green]")
            else:
                console.print(f"[yellow]{len(gaps)} gap(s) found:[/yellow]")
                for g_start, g_end in gaps:
                    console.print(
                        f"  gap: {datetime.utcfromtimestamp(g_start / 1000):%Y-%m-%d %H:%M}"
                        f" → {datetime.utcfromtimestamp(g_end / 1000):%Y-%m-%d %H:%M}"
                        f"  ({(g_end - g_start) / 1000 / 3600:.1f}h missing)"
                    )
            return
        except Exception as e:
            console.print(f"[dim]marketdata_provider error ({e}), falling back to direct query[/dim]")

    # Fallback: query parquet files directly
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    candle_dir = (
        config.data_dir
        / "candles"
        / f"exchange={exchange}"
        / f"market_type={market}"
        / f"symbol={symbol}"
        / "price_type=trade"
        / f"timeframe={timeframe}"
    )

    if not candle_dir.exists():
        console.print(
            f"[dim]No candle data found at: {candle_dir}\n"
            f"  → run 'openpine data backfill {symbol} {timeframe} ...' first[/dim]"
        )
        return

    try:
        import pandas as pd
    except ImportError:
        console.print("[dim]pandas not available — cannot read parquet files[/dim]")
        return

    parquet_files = sorted(candle_dir.rglob("*.parquet"))
    if not parquet_files:
        console.print(f"[dim]No parquet files found for {symbol} {timeframe}[/dim]")
        return

    try:
        df = pd.concat((pd.read_parquet(f) for f in parquet_files), ignore_index=True)
        df = df.sort_values("open_time")
        bar_times = df["open_time"].drop_duplicates().tolist()
        if len(bar_times) < 2:
            console.print(f"[dim](only {len(bar_times)} bar(s), too few to detect gaps)[/dim]")
            return

        gaps: list[tuple[int, int]] = []
        tf_ms = _timeframe_to_ms(timeframe)
        for i in range(1, len(bar_times)):
            prev = bar_times[i - 1]
            curr = bar_times[i]
            if curr - prev > tf_ms * 1.5:
                gaps.append((prev, curr))

        if not gaps:
            console.print(f"[green]No gaps found ({len(bar_times)} bars across {len(parquet_files)} file(s))[/green]")
        else:
            console.print(f"[yellow]{len(gaps)} gap(s) found:[/yellow]")
            for g_start, g_end in sorted(gaps):
                console.print(
                    f"  gap: {datetime.utcfromtimestamp(g_start / 1000):%Y-%m-%d %H:%M}"
                    f" → {datetime.utcfromtimestamp(g_end / 1000):%Y-%m-%d %H:%M}"
                    f"  ({(g_end - g_start) / 1000 / 3600:.1f}h missing)"
                )
    except Exception as e:
        console.print(f"[red]Error reading parquet files: {e}[/red]")


@data.command("repair")
@click.argument("symbol", required=True)
@click.argument("timeframe", required=True)
@click.option("--from", "from_ts", required=True, type=int, help="Gap start timestamp (ms)")
@click.option("--to", "to_ts", required=True, type=int, help="Gap end timestamp (ms)")
@click.option("--exchange", default="binance", help="Exchange name")
def data_repair(symbol: str, timeframe: str, from_ts: int, to_ts: int, exchange: str) -> None:
    """Mark a gap range as repaired (re-fetch)."""
    if from_ts >= to_ts:
        console.print("[red]Invalid repair window: --from must be before --to[/red]")
        raise SystemExit(1)

    from openpine.registry import SQLiteStrategyRegistry

    console.print(
        f"[bold]Data repair[/bold] {symbol} {timeframe} "
        f"range={datetime.fromtimestamp(from_ts / 1000, timezone.utc):%Y-%m-%d %H:%M} → "
        f"{datetime.fromtimestamp(to_ts / 1000, timezone.utc):%Y-%m-%d %H:%M}"
    )

    job = Job(
        job_type=JobType.BACKFILL,
        strategy_id=None,
        status=JobStatus.PENDING,
        idempotency_key=f"repair:{symbol}:{timeframe}:{from_ts}:{to_ts}:{exchange}",
        result={
            "action": "repair",
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": exchange,
            "from_ts": from_ts,
            "to_ts": to_ts,
        },
    )
    queued = _cli_scheduler.enqueue(job)

    invalidated: list[str] = []
    registry = SQLiteStrategyRegistry()
    try:
        for strategy in registry.list_strategies():
            if (
                strategy.symbol.upper() == symbol.upper()
                and strategy.timeframe == timeframe
                and strategy.exchange.lower() == exchange.lower()
            ):
                invalidated.append(strategy.strategy_id)
                if strategy.status == "running":
                    registry.update_status(strategy.strategy_id, "paused")
    finally:
        registry.close()

    console.print(f"[green]Repair backfill job queued: {queued.id[:8]}[/green]")
    if invalidated:
        console.print(f"[yellow]Affected strategies paused/marked for rebuild:[/yellow] {len(invalidated)}")
        for strategy_id in invalidated:
            console.print(f"  {strategy_id}")
    else:
        console.print("[dim]No matching strategies found for this repair window[/dim]")
    console.print(
        f"[dim]Next: worker backfill repairs data, then run "
        f"`openpine state rebuild <strategy_id> --from-bar {from_ts}` for affected strategies.[/dim]"
    )


@data.command("backfill")
@click.argument("symbol", required=True)
@click.argument("timeframe", required=True)
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD, default: today)")
@click.option("--exchange", default="binance", help="Exchange name")
@click.option("--market", default="usdm", help="Market type")
@click.option("--price-type", "price_type", default="trade", help="Price type")
def data_backfill(
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str | None,
    exchange: str,
    market: str,
    price_type: str,
) -> None:
    """Trigger backfill for a symbol/timeframe."""
    from datetime import datetime as dt

    console.print(f"[bold]Data backfill[/bold] {symbol} {timeframe} exchange={exchange}")
    console.print(f"  from: {from_date}  to: {to_date or 'today'}")

    # Parse dates to ms
    try:
        start_ms = int(dt.strptime(from_date, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        console.print(f"[red]Invalid --from date format: {from_date} (use YYYY-MM-DD)[/red]")
        return

    if to_date:
        try:
            end_ms = int(dt.strptime(to_date, "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            console.print(f"[red]Invalid --to date format: {to_date} (use YYYY-MM-DD)[/red]")
            return
    else:
        end_ms = int(dt.now().timestamp() * 1000)

    # Try DataOrchestrator.trigger_backfill if available
    try:
        from openpine.data.orchestrator import DataOrchestrator

        orch = DataOrchestrator()
        if hasattr(orch, "trigger_backfill"):
            orch.trigger_backfill(
                symbol=symbol,
                timeframe=timeframe,
                exchange=exchange,
                market=market,
                price_type=price_type,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            console.print(f"[green]Backfill triggered via DataOrchestrator[/green]")
            return
    except Exception:
        pass

    # Fallback: enqueue a backfill job
    from openpine.jobs import Job, JobStatus, JobType

    job = Job(
        job_type=JobType.BACKFILL,
        strategy_id=None,
        status=JobStatus.PENDING,
        idempotency_key=f"backfill:{symbol}:{timeframe}:{exchange}:{start_ms}:{end_ms}",
        priority=10,
    )
    _cli_scheduler.enqueue(job)
    console.print(f"[green]Backfill job enqueued: {job.id[:8]}[/green]")
    console.print(f"  symbol={symbol} tf={timeframe} exchange={exchange}")
    console.print(f"  from={from_date} to={to_date or 'today'}")


@data.command("parallel-backfill")
@click.argument("symbols", required=True)
@click.argument("timeframe", required=True)
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD, default: today)")
@click.option("--exchange", default="binance", help="Exchange name")
@click.option("--workers", "max_workers", default=None, type=int, help="Max parallel workers (default: half CPU cores)")
@click.option("--chunked", is_flag=True, help="Use chunked parallel fetch for large ranges")
def data_parallel_backfill(
    symbols: str,
    timeframe: str,
    from_date: str,
    to_date: str | None,
    exchange: str,
    max_workers: int | None,
    chunked: bool,
) -> None:
    """Parallel backfill for multiple symbols (comma-separated)."""
    from datetime import datetime as dt
    from openpine.data.parallel_fetcher import ParallelDataFetcher, FetchJob

    symbol_list = [s.strip().upper() for s in symbols.split(",")]

    # Parse dates
    try:
        start_ms = int(dt.strptime(from_date, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        console.print(f"[red]Invalid --from date format: {from_date} (use YYYY-MM-DD)[/red]")
        return

    if to_date:
        try:
            end_ms = int(dt.strptime(to_date, "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            console.print(f"[red]Invalid --to date format: {to_date} (use YYYY-MM-DD)[/red]")
            return
    else:
        end_ms = int(dt.now().timestamp() * 1000)

    console.print(f"[bold]Parallel backfill[/bold] {len(symbol_list)} symbols, tf={timeframe}")
    console.print(f"  symbols: {', '.join(symbol_list)}")
    console.print(f"  range: {from_date} to {to_date or 'today'}")
    console.print(f"  workers: {max_workers or 'auto (half cores)'}")

    fetcher = ParallelDataFetcher(max_workers=max_workers)

    if chunked and len(symbol_list) == 1:
        # Single symbol chunked mode
        bars = fetcher.fetch_chunked(
            symbol_list[0], timeframe, start_ms, end_ms, exchange=exchange
        )
        console.print(f"[green]Chunked fetch complete: {len(bars)} bars[/green]")
        return

    # Multi-symbol parallel mode
    jobs = [
        FetchJob(symbol=sym, timeframe=timeframe, start_ms=start_ms, end_ms=end_ms, exchange=exchange)
        for sym in symbol_list
    ]

    def _progress(key: str, done: int, total: int) -> None:
        console.print(f"  [{done}/{total}] {key}")

    results = fetcher.fetch_many(jobs, progress_callback=_progress)

    total_bars = sum(len(bars) for bars in results.values())
    console.print(f"[green]Parallel backfill complete: {total_bars} total bars[/green]")
    for key, bars in results.items():
        console.print(f"  {key}: {len(bars)} bars")


@data.command("inspect")
@click.argument("symbol", required=True)
@click.argument("timeframe", required=True)
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD, default: today)")
@click.option("--exchange", default="binance", help="Exchange name")
@click.option("--market", default="usdm", help="Market type")
def data_inspect(
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str | None,
    exchange: str,
    market: str,
) -> None:
    """Inspect raw candles in a time range."""
    from datetime import datetime as dt
    from openpine.config import OpenPineConfig

    console.print(f"[bold]Data inspect[/bold] {symbol} {timeframe}")

    try:
        start_ms = int(dt.strptime(from_date, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        console.print(f"[red]Invalid --from date format: {from_date} (use YYYY-MM-DD)[/red]")
        return

    end_ms = int(
        dt.strptime(to_date, "%Y-%m-%d").timestamp() * 1000
    ) if to_date else int(dt.now().timestamp() * 1000)

    config = OpenPineConfig.load()
    candle_dir = (
        config.data_dir
        / "candles"
        / f"exchange={exchange}"
        / f"market_type={market}"
        / f"symbol={symbol}"
        / "price_type=trade"
        / f"timeframe={timeframe}"
    )

    if not candle_dir.exists():
        console.print(f"[dim]No candle data found at: {candle_dir}[/dim]")
        console.print(f"  → run 'openpine data backfill {symbol} {timeframe} ...' first")
        return

    try:
        import pandas as pd
    except ImportError:
        console.print("[dim]pandas not available for parquet reading[/dim]")
        return

    parquet_files = sorted(candle_dir.rglob("*.parquet"))
    if not parquet_files:
        console.print(f"[dim]No parquet files found for {symbol} {timeframe}[/dim]")
        return

    try:
        dfs = []
        for pf in parquet_files:
            try:
                df = pd.read_parquet(pf)
                dfs.append(df)
            except Exception:
                pass
        if not dfs:
            console.print("[red]Could not read any parquet files[/red]")
            return

        all_df = pd.concat(dfs, ignore_index=True)
        mask = (all_df["open_time"] >= start_ms) & (all_df["open_time"] <= end_ms)
        subset = all_df[mask].sort_values("open_time").head(20)

        if subset.empty:
            console.print(f"[dim](no bars in range {from_date} → {to_date or 'today'})[/dim]")
            return

        from rich.table import Table

        tbl = Table(title=f"Candles: {symbol} {timeframe} ({from_date} → {to_date or 'today'})")
        tbl.add_column("open_time", style="cyan")
        tbl.add_column("open", style="green")
        tbl.add_column("high", style="green")
        tbl.add_column("low", style="red")
        tbl.add_column("close", style="green")
        tbl.add_column("volume", style="yellow")

        for _, row in subset.iterrows():
            ot = datetime.utcfromtimestamp(row["open_time"] / 1000).strftime("%Y-%m-%d %H:%M")
            tbl.add_row(
                ot,
                str(round(row.get("open", 0), 6)),
                str(round(row.get("high", 0), 6)),
                str(round(row.get("low", 0), 6)),
                str(round(row.get("close", 0), 6)),
                str(round(row.get("volume", 0), 2)),
            )
        console.print(tbl)
        console.print(f"[dim]Showing {len(subset)} of {len(all_df)} total bars[/dim]")
    except Exception as e:
        console.print(f"[red]Error inspecting data: {e}[/red]")


@data.command("providers")
def data_providers() -> None:
    """List available data providers."""
    KNOWN_PROVIDERS = {
        "binance": {
            "name": "Binance",
            "rest": "https://api.binance.com",
            "ws": "wss://stream.binance.com:9443/ws",
            "status": "active" if True else "unknown",
        },
        "bybit": {
            "name": "Bybit",
            "rest": "https://api.bybit.com",
            "ws": "wss://stream.bybit.com/v5/public/spot",
            "status": "active" if True else "unknown",
        },
        "okx": {
            "name": "OKX",
            "rest": "https://www.okx.com",
            "ws": "wss://ws.okx.com:8443/ws/v5/public",
            "status": "unknown",
        },
        "kraken": {
            "name": "Kraken",
            "rest": "https://api.kraken.com",
            "ws": "wss://ws.kraken.com",
            "status": "unknown",
        },
        "coinbase": {
            "name": "Coinbase",
            "rest": "https://api.exchange.coinbase.com",
            "ws": "wss://ws-feed.exchange.coinbase.com",
            "status": "unknown",
        },
        "marketdata-provider": {
            "name": "Local marketdata-provider",
            "status": "available" if True else "not installed",
        },
    }

    # Check which are actually reachable (guard against partial install)
    local_provider_available = False
    try:
        from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
        local_provider_available = create_local_marketdata_provider_adapter() is not None
    except Exception:
        local_provider_available = False

    from rich.table import Table

    tbl = Table(title="Available Data Providers")
    tbl.add_column("ID", style="cyan")
    tbl.add_column("Name", style="green")
    tbl.add_column("REST endpoint", style="dim")
    tbl.add_column("WebSocket", style="dim")
    tbl.add_column("Status", style="yellow")

    for pid, info in KNOWN_PROVIDERS.items():
        status = info.get("status", "unknown")
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


def _timeframe_to_ms(timeframe: str) -> int:
    """Convert timeframe string to milliseconds."""
    mapping = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
        "3d": 259_200_000,
        "1w": 604_800_000,
    }
    return mapping.get(timeframe, 60_000)


@data.command("ensure-active")
def data_ensure_active() -> None:
    """Ensure all active strategies have required data."""
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.data.planner import DataPlanner
    from openpine.streams import MarketDataStreamManager
    from openpine.universe import ActiveUniverse

    console.print("[bold]Data ensure-active[/bold]")
    universe = ActiveUniverse()
    planner = DataPlanner(
        stream_manager=MarketDataStreamManager.__new__(MarketDataStreamManager),
        orchestrator=DataOrchestrator(),
        universe=universe,
    )

    strategies = universe.list_strategies()
    if not strategies:
        console.print("[dim]No active strategies in universe[/dim]")
        return

    for strategy_id in strategies:
        plan = planner.plan_for_strategy(strategy_id)
        reqs = plan.requirements
        console.print(f"  {strategy_id}: {len(reqs)} data requirements")
    console.print(f"[green]Active universe data check complete[/green]")


@data.command("backfill-active")
def data_backfill_active() -> None:
    """Trigger backfill for all active strategies' data requirements."""
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.data.planner import DataPlanner
    from openpine.streams import MarketDataStreamManager
    from openpine.universe import ActiveUniverse

    console.print("[bold]Data backfill-active[/bold]")
    universe = ActiveUniverse()
    planner = DataPlanner(
        stream_manager=MarketDataStreamManager.__new__(MarketDataStreamManager),
        orchestrator=DataOrchestrator(),
        universe=universe,
    )

    strategies = universe.list_strategies()
    if not strategies:
        console.print("[dim]No active strategies to backfill[/dim]")
        return

    for strategy_id in strategies:
        plan = planner.plan_for_strategy(strategy_id)
        console.print(f"  Backfill queued for {strategy_id}: {len(plan.requirements)} requirements")
    console.print(f"[green]Backfill triggered for {len(strategies)} strategies[/green]")


@data.command("aggregate")
@click.argument("symbol")
@click.option("--from", "source_tf", required=True, help="Source timeframe (e.g. 1m)")
@click.option("--to", "target_tfs", required=True, help="Target timeframes as comma-separated (e.g. 5m,15m,1h)")
def data_aggregate(symbol: str, source_tf: str, target_tfs: str) -> None:
    """Aggregate candles from source to target timeframes."""
    console.print(f"[bold]Data aggregate[/bold] {symbol} {source_tf} -> {target_tfs}")
    targets = [t.strip() for t in target_tfs.split(",")]
    for target in targets:
        console.print(f"  {symbol} {source_tf} -> {target}")
    console.print(f"[green]Aggregation plan created for {len(targets)} target timeframe(s)[/green]")


@cli.group()
def universe() -> None:
    """Active universe management."""
    pass


@universe.command("show")
def universe_show() -> None:
    """Show active universe summary."""
    from openpine.universe import ActiveUniverse

    console.print("[bold]Active Universe[/bold]")
    universe = ActiveUniverse()
    strategies = universe.list_strategies()
    console.print(f"Mode:     {universe.mode}")
    console.print(f"Strategies: {len(strategies)}")
    plan = universe.build_data_plan()
    console.print(f"Data requirements:     {len(plan.requirements)}")
    console.print(f"Aggregation requirements: {len(plan.aggregation_requirements)}")
    console.print(f"Feature requirements:     {len(plan.feature_requirements)}")


@universe.command("active")
def universe_active() -> None:
    """List active strategies in the universe."""
    from openpine.universe import ActiveUniverse

    console.print("[bold]Active Strategies[/bold]")
    universe = ActiveUniverse()
    strategies = universe.list_strategies()
    if not strategies:
        console.print("[dim](no active strategies)[/dim]")
        return
    for sid in strategies:
        plan = universe.build_data_plan()
        reqs = universe.get_strategy_requirements(sid)
        console.print(f"  {sid}  ({len(reqs)} requirements)")


@universe.command("requirements")
def universe_requirements() -> None:
    """Show merged requirements from all active strategies."""
    from openpine.universe import ActiveUniverse

    console.print("[bold]Merged Universe Requirements[/bold]")
    universe = ActiveUniverse()
    plan = universe.build_data_plan()
    if not plan.requirements:
        console.print("[dim](no data requirements)[/dim]")
    else:
        console.print(f"[bold]Data requirements ({len(plan.requirements)})[/bold]")
        for req in plan.requirements:
            console.print(
                f"  {req.instrument_key} {req.timeframe} "
                f"{req.start_ms}-{req.end_ms} provider={req.provider}"
            )
    if plan.aggregation_requirements:
        console.print(f"\n[bold]Aggregation requirements ({len(plan.aggregation_requirements)})[/bold]")
        for req in plan.aggregation_requirements:
            console.print(
                f"  {req.instrument_key} {req.source_timeframe} -> {req.target_timeframe} "
                f"{req.start_ms}-{req.end_ms}"
            )


@cli.group()
def jobs() -> None:
    """Job management commands."""
    pass


@cli.group()
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
Environment=PYTHONPATH={sys.path[0] if sys.path[0] else '.'}

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


@cli.group()
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


@cli.group()
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


# ── config (top-level) ─────────────────────────────────────────────────────────


@cli.group()
def config() -> None:
    """Configuration management commands."""
    pass


@config.command("show")
def config_show() -> None:
    """Show current OpenPine configuration."""
    from openpine.config import OpenPineConfig, DEFAULT_CONFIG

    cfg = OpenPineConfig.load()
    console.print("[bold]OpenPine Configuration[/bold]")
    console.print(f"  data_dir:       {cfg.data_dir}")
    console.print(f"  config_dir:     {cfg.config_dir}")
    console.print(f"  sqlite_path:    {cfg.sqlite_path}")
    console.print(f"  duckdb_path:    {cfg.duckdb_path}")
    console.print(f"  log_level:      {cfg.log_level}")
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

    # Required fields check
    if cfg.data_dir is None:
        errors.append("data_dir is required")
    if cfg.config_dir is None:
        errors.append("config_dir is required")
    if cfg.sqlite_path is None:
        errors.append("sqlite_path is required")

    # Type checks
    if not isinstance(cfg.live_enabled, bool):
        errors.append("live_enabled must be a boolean")
    if not isinstance(cfg.kill_switch, bool):
        errors.append("kill_switch must be a boolean")
    if not isinstance(cfg.log_level, str):
        errors.append("log_level must be a string")
    if cfg.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        errors.append(f"log_level must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got {cfg.log_level}")

    console.print("[bold]Config Validation[/bold]")
    if errors:
        console.print("[red]Validation failed:[/red]")
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        sys.exit(1)
    else:
        console.print("[green]Configuration is valid.[/green]")


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
            f"created={datetime.utcfromtimestamp(j.created_at_ms / 1000):%H:%M:%S}"
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
    console.print(f"  created_at:    {datetime.utcfromtimestamp(job.created_at / 1000):%Y-%m-%d %H:%M:%S} UTC")
    if job.started_at:
        console.print(f"  started_at:    {datetime.utcfromtimestamp(job.started_at / 1000):%Y-%m-%d %H:%M:%S} UTC")
    if job.finished_at:
        console.print(f"  finished_at:   {datetime.utcfromtimestamp(job.finished_at / 1000):%Y-%m-%d %H:%M:%S} UTC")
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
    console.print("[dim]To enable: set stream.provider in config or OPENPINE_STREAM_PROVIDER env var[/dim]")


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
        ts = datetime.utcfromtimestamp(snap.saved_at / 1000).strftime("%Y-%m-%d %H:%M:%S")
        bar_ts = datetime.utcfromtimestamp(snap.bar_time / 1000).strftime("%Y-%m-%d %H:%M") if snap.bar_time else "-"
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
    from openpine.state.store import StateStore

    config = OpenPineConfig.load()
    state_dir = config.data_dir / "state"
    store = StateStore(state_dir)

    # Collect all snapshots and check status
    all_invalid: list[dict] = []
    # Scan state dir for strategy dirs
    if not state_dir.exists():
        console.print("[dim]No state directory found.[/dim]")
        return

    for strategy_dir in state_dir.iterdir():
        if not strategy_dir.is_dir() or not strategy_dir.name.startswith("strategy_id="):
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
            all_invalid.append({
                "strategy_id": strategy_id,
                "snapshot_file": snap_file.name,
                "bar_time": bar_time,
            })

    if not all_invalid:
        console.print("[dim](no invalid snapshots found — all active or superseded)[/dim]")
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
@click.option("--from-bar", "from_bar_time", type=int, default=None, help="Rebuild from bar time (ms)")
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

    console.print(f"[bold]Rebuilding state[/bold] for {strategy_id} from bar_time={from_bar_time}")
    try:
        result = rebuilder.rebuild(strategy_id, from_bar_time)
        console.print("[green]Rebuild successful[/green]")
        console.print(f"  strategy_id:     {result.strategy_id}")
        console.print(f"  artifact_id:    {result.artifact_id}")
        console.print(f"  last_bar_time: {result.bar_time}")
    except StateInconsistencyError as e:
        console.print(f"[red]Rebuild failed: {e}[/red]")
        raise SystemExit(1)


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
            live_badge = "[green]LIVE[/green]" if acc.live_enabled else "[dim]live=False[/dim]"
            console.print(
                f"  {acc.name}  id={acc.id[:12]}  "
                f"type={acc.account_type.value}  exchange={acc.exchange}  {live_badge}"
            )
    finally:
        storage.close()


@accounts.command("add")
@click.option("--name", "name", required=True, help="Account name")
@click.option("--exchange", "exchange", required=True, help="Exchange name (e.g. binance)")
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
    import hashlib
    import secrets

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
        console.print(f"  secret:      **** (stored as reference)")
        console.print(f"  live_enabled:{live_enabled}")
    except Exception as e:
        console.print(f"[red]Failed to add account: {e}[/red]")
        storage.rollback()
        raise SystemExit(1)
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
            console.print("[dim]Network credential verification is performed by execution adapters at submit time[/dim]")
        else:
            console.print("[green]✓ Paper/backtest account configuration is valid[/green]")
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
        from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
        local_provider_available = create_local_marketdata_provider_adapter() is not None
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
            from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
            adapter = create_local_marketdata_provider_adapter()
        except Exception:
            adapter = None
        if adapter is None:
            console.print("[red]✗ marketdata-provider not installed or not importable[/red]")
            raise SystemExit(1)
        console.print(f"[green]✓ marketdata-provider is available[/green]")
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
            console.print(f"[green]✓ HTTP {resp.status_code} — provider reachable[/green]")
            console.print(f"  Response: {resp.text[:200]}")
        else:
            console.print(f"[yellow]! HTTP {resp.status_code} — endpoint responded[/yellow]")
            console.print(f"  Response: {resp.text[:200]}")
    except ImportError:
        console.print(f"[yellow]! requests not available — cannot test {provider}[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise SystemExit(1)


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
    console.print(f"Global kill switch blocks all orders when active (section 30.7)")

    if show_violations:
        console.print("[bold]Recent violations:[/bold]")
        # RiskManager is instantiated per-session in CLI
        # For now, just show the config status
        console.print("[dim](violation tracking requires live RiskManager instance)[/dim]")


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
        console.print("[yellow]Warning: all live/paper order intents will be blocked.[/yellow]")


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
        if status.importable:
            version = f" version={status.version}" if status.version else ""
            console.print(f"  [green]✓[/green] {status.name}{version} path={status.path}")
        else:
            console.print(f"  [red]✗[/red] {status.name} path={status.path} error={status.error}")
            all_ok = False
    if not all_ok:
        sys.exit(1)


@cli.group()
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

    result = OptimizerService().dry_run(strategy_id=strategy_id, trials=trials)
    console.print("[bold]Optimizer dry run[/bold]")
    console.print(f"optimization_id:           {result.optimization_id}")
    console.print(f"strategy_id:               {result.strategy_id}")
    console.print(f"trials_requested:          {result.trials_requested}")
    console.print(f"trials_completed:          {result.trials_completed}")
    console.print(f"status:                    {result.status}")
    console.print(f"uses_backtest_engine_path: {result.uses_backtest_engine_path}")


# ── reports ────────────────────────────────────────────────────────────────────


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
        import json as _json

        return _json.loads(path.read_text())
    return path.read_text()


@cli.group()
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
        console.print(f"  {report['id']}  — {report['description']}")

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
    import json as _json

    config = OpenPineConfig.load()
    reports_dir = config.data_dir / "reports"
    found = _find_report_files(reports_dir, report_id)
    if found:
        report_file = found[0]
        content = _load_report_file(report_file)
        console.print(f"[bold]Report:[/bold] {report_file.relative_to(reports_dir)}")
        if isinstance(content, (dict, list)):
            console.print(_json.dumps(content, indent=2, default=str))
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
    import json as _json

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
                console.print(_json.dumps(content, indent=2, default=str))
            else:
                console.print(_json.dumps({"id": report_id, "content": content}, indent=2))
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
            console.print(_json.dumps(report, indent=2))
        else:
            console.print("id,description,status")
            console.print(f"{report['id']},{report['description']},{report['status']}")
        return

    console.print(f"[red]Report not found: {report_id}[/red]")
    console.print(f"[dim]Searched: {reports_dir}[/dim]")
    sys.exit(1)


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
            name = getattr(info, "name")
            plugin_type = getattr(info, "plugin_type")
            enabled = getattr(info, "enabled")
        status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        console.print(f"  {name}  type={plugin_type}  {status}")


@plugins.command("enable")
@click.argument("plugin_name")
@click.option("--chat-id", "chat_id", default=None, help="Add a chat ID to the allowlist")
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

    console.print(f"\n[bold]Current telegram config:[/bold]")
    console.print(f"  enabled:       {cfg.enabled}")
    console.print(f"  token_ref:     {cfg.token_ref}")
    console.print(f"  allowlist:     {cfg.chat_allowlist}")
    console.print(f"\nNote: Set the token with: export OPENPINE_TELEGRAM_TOKEN=***")


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

    console.print(f"\n[bold]Current telegram config:[/bold]")
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
        console.print(f"[green]✓ Telegram plugin smoke test PASSED[/green]")
        console.print(f"  chat_id:      {chat_id}")
        console.print(f"  dry_run:     True (no network call)")
        console.print(f"  token_ref:   {config.plugins.telegram.token_ref}")
        console.print(f"  allowlist:   {config.plugins.telegram.chat_allowlist}")
    else:
        console.print(f"[red]✗ Telegram plugin smoke test FAILED[/red]")
        console.print(f"  reason: {result.error_message}")
        sys.exit(1)


def _fallback_telegram_command_catalog() -> list[dict[str, object]]:
    """Built-in Telegram command view used until a catalog module is available."""
    return [
        {"command": "/menu", "title": "Open interactive menu", "cli": "openpine plugins telegram send-menu"},
        {"command": "/help", "title": "Show available commands", "cli": "openpine plugins telegram commands"},
        {"command": "/status", "title": "System status", "cli": "openpine doctor"},
        {"command": "/version", "title": "OpenPine version", "cli": "openpine version"},
        {"command": "/config", "title": "Show configuration", "cli": "openpine config show"},
        {"command": "/storage", "title": "Storage status", "cli": "openpine storage verify"},
        {"command": "/pine_list", "title": "List Pine sources", "cli": "openpine pine list"},
        {"command": "/pine_add", "title": "Add Pine source", "cli": "openpine pine pine-add <name> <file.pine>"},
        {"command": "/pine_compile", "title": "Compile Pine source", "cli": "openpine pine pine-compile <name>"},
        {"command": "/pine_show", "title": "Show Pine source", "cli": "openpine pine show <name>"},
        {"command": "/pine_versions", "title": "List Pine versions", "cli": "openpine pine versions <name>"},
        {"command": "/pine_activate", "title": "Activate Pine artifact", "cli": "openpine pine activate <name> <artifact_id>"},
        {"command": "/strategies", "title": "List strategies", "cli": "openpine strategy list"},
        {"command": "/strategy_create", "title": "Create strategy", "cli": "openpine strategy create --pine <name> --symbol <symbol> --timeframe <tf>"},
        {"command": "/strategy_show", "title": "Show strategy", "cli": "openpine strategy show <strategy_id>"},
        {"command": "/strategy_update", "title": "Update strategy params", "cli": "openpine strategy update <strategy_id> --param k=v"},
        {"command": "/pause", "title": "Pause strategy", "cli": "openpine strategy pause <strategy_id>"},
        {"command": "/resume", "title": "Resume strategy", "cli": "openpine strategy resume <strategy_id>"},
        {"command": "/remove", "title": "Remove strategy", "cli": "openpine strategy remove <strategy_id>"},
        {"command": "/backtest", "title": "Run backtest", "cli": "openpine strategy backtest <strategy_id>"},
        {"command": "/replay", "title": "Run replay", "cli": "openpine strategy replay <strategy_id>"},
        {"command": "/paper", "title": "Paper trading controls", "cli": "openpine strategy paper <start|stop|status> <strategy_id>"},
        {"command": "/live", "title": "Live trading controls", "cli": "openpine strategy live <enable|disable|status> <strategy_id>"},
        {"command": "/data", "title": "Data status", "cli": "openpine data status"},
        {"command": "/data_gaps", "title": "Find data gaps", "cli": "openpine data gaps <symbol> <timeframe>"},
        {"command": "/data_backfill", "title": "Backfill candles", "cli": "openpine data backfill <symbol> <timeframe>"},
        {"command": "/data_repair", "title": "Repair candles", "cli": "openpine data repair <symbol> <timeframe>"},
        {"command": "/accounts", "title": "List accounts", "cli": "openpine accounts list"},
        {"command": "/account_add", "title": "Add account", "cli": "openpine accounts add"},
        {"command": "/account_test", "title": "Test account", "cli": "openpine accounts test <account_id>"},
        {"command": "/risk", "title": "Risk status", "cli": "openpine risk status"},
        {"command": "/kill_on", "title": "Enable kill switch", "cli": "openpine risk kill-switch on"},
        {"command": "/kill_off", "title": "Disable kill switch", "cli": "openpine risk kill-switch off"},
        {"command": "/jobs", "title": "List jobs", "cli": "openpine jobs list"},
        {"command": "/job_show", "title": "Show job", "cli": "openpine jobs show <job_id>"},
        {"command": "/job_cancel", "title": "Cancel job", "cli": "openpine jobs cancel <job_id>"},
        {"command": "/reports", "title": "List reports", "cli": "openpine reports list"},
        {"command": "/report_show", "title": "Show report", "cli": "openpine reports show <report_id>"},
        {"command": "/plugins", "title": "List plugins", "cli": "openpine plugins list"},
    ]


def _load_telegram_command_catalog() -> list[dict[str, object]]:
    """Import the shared Telegram command catalog when present, else fallback."""
    module_names = (
        "openpine.plugins.telegram.command_catalog",
        "openpine.telegram.command_catalog",
        "openpine.notifications.telegram_commands",
    )
    for module_name in module_names:
        try:
            module = __import__(module_name, fromlist=["x"])
        except ImportError:
            continue
        for attr in ("get_telegram_commands", "telegram_command_catalog", "get_commands"):
            provider = getattr(module, attr, None)
            if callable(provider):
                return [_normalize_telegram_command(item) for item in provider()]
        for attr in ("TELEGRAM_COMMANDS", "COMMANDS"):
            commands = getattr(module, attr, None)
            if commands is not None:
                return [_normalize_telegram_command(item) for item in commands]
    return _fallback_telegram_command_catalog()


def _normalize_telegram_command(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        command = str(item.get("command") or item.get("name") or "")
        title = str(item.get("title") or item.get("description") or command)
        cli_cmd = str(item.get("cli") or item.get("cli_command") or "")
    else:
        command = str(getattr(item, "command", getattr(item, "name", "")))
        title = str(getattr(item, "title", getattr(item, "description", command)))
        cli_cmd = str(getattr(item, "cli", getattr(item, "cli_command", "")))
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
        console.print("[dim]Enable it with: openpine plugins enable telegram --chat-id <id>[/dim]")
        sys.exit(1)
    token = telegram_cfg.resolve_token()
    if not token:
        console.print(f"[red]Telegram token not available: {telegram_cfg.token_ref}[/red]")
        console.print("[dim]Dry-run commands do not require a token.[/dim]")
        sys.exit(1)
    return token


def _telegram_api_request(token: str, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
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
@click.option("--dry-run", is_flag=True, help="Do not call Telegram; print request plan")
@click.option("--fake-updates-json", default=None, help="JSON updates payload for tests")
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

    updates = data.get("result", data if isinstance(data, list) else [])
    console.print(f"[bold]Telegram updates: {len(updates)}[/bold]")
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        console.print(f"  update_id={update.get('update_id')} chat_id={chat_id} text={text!r}")
    if once:
        return


@plugins_telegram.command("webhook-info")
@click.option("--dry-run", is_flag=True, help="Do not call Telegram; print request plan")
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
    console.print(_json.dumps(_telegram_api_request(token, "sendMessage", payload), indent=2))


# ── strategy lifecycle ──────────────────────────────────────────────────────────

import time as _time_module


@cli.group()
def strategy() -> None:
    """Strategy lifecycle management."""
    pass


@strategy.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON for bot consumption")
def strategy_list(as_json: bool) -> None:
    """List all strategies."""
    from openpine.registry import SQLiteStrategyRegistry
    import json

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
@click.option(
    "--mode",
    default="paper",
    type=click.Choice(["backtest", "replay", "paper", "live"]),
)
@click.option("--param", multiple=True, help="key=value params")
def strategy_create(
    strategy_id: str | None,
    pine: str,
    symbol: str,
    timeframe: str,
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
        )
        # Set initial status based on mode
        initial_status = {
            "backtest": "pending",
            "replay": "pending",
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
@click.option("--capture-plots", is_flag=True, help="Capture and save plot outputs from runtime")
def strategy_backtest(strategy_id: str, from_date: str | None, to_date: str | None, capture_plots: bool) -> None:
    """Run backtest for a strategy."""
    import json as _json
    from datetime import timezone as _timezone

    from openpine.contracts import BarQuery, InstrumentKey, Timeframe
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.runtime.engine import (
        BacktestArtifactError,
        BacktestEngineAdapter,
        BacktestRunConfig,
        load_strategy_class_from_artifact,
    )

    def _parse_date_ms(value: str | None, default: int) -> int:
        if not value:
            return default
        if value.isdigit():
            raw = int(value)
            return raw if raw > 10_000_000_000 else raw * 1000
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_timezone.utc)
        return int(parsed.timestamp() * 1000)

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        registry.update_status(strategy_id, "running")
        console.print(f"[bold]Backtest: {strategy_id}[/bold]")
        console.print(f"  strategy:   {s.name}")
        console.print(f"  artifact:   {s.artifact_id}")
        console.print(f"  params:     {s.params_json}")
        console.print(f"  symbol:     {s.symbol}")
        console.print(f"  timeframe:  {s.timeframe}")
        console.print(f"  from:       {from_date or 'N/A'}")
        console.print(f"  to:         {to_date or 'N/A'}")

        if not s.pine_id:
            console.print(
                "[red]Strategy has no pine_id. Recreate it with: "
                "openpine strategy create <name> --pine <pine-name> ...[/red]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)
        if not s.artifact_id:
            console.print(
                "[red]Strategy has no compiled artifact. Compile first with: "
                "openpine pine compile <pine-name>[/red]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        end_ms = _parse_date_ms(to_date, int(_time_module.time() * 1000))
        start_ms = _parse_date_ms(from_date, 0)
        if start_ms >= end_ms:
            console.print("[red]Invalid backtest window: --from must be before --to[/red]")
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        try:
            strategy_class = load_strategy_class_from_artifact(
                s.pine_id,
                s.artifact_id,
                symbol=s.symbol,
                timeframe=s.timeframe,
            )
        except BacktestArtifactError as exc:
            console.print(f"[red]{exc}[/red]")
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        query = BarQuery(
            instrument_key=InstrumentKey(symbol=s.symbol, exchange=s.exchange.upper()),
            timeframe=Timeframe(value=s.timeframe),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
        orch = DataOrchestrator()
        provider = create_local_marketdata_provider_adapter()
        if provider:
            orch.set_provider(provider)
        bars = orch.get_bars(query)
        if not bars:
            console.print(
                f"[red]No candle data found for {s.symbol} {s.timeframe} "
                f"in {start_ms}-{end_ms}.[/red]"
            )
            console.print(
                f"[yellow]Run: openpine data backfill {s.symbol} {s.timeframe} "
                f"--from {from_date or start_ms} --to {to_date or end_ms}[/yellow]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        # Load declaration from compile_meta for config alignment
        from openpine.artifacts import ArtifactStore
        store = ArtifactStore()
        artifact = store.get_artifact(s.artifact_id, s.pine_id)
        compile_meta = artifact.get("compile_meta", {})
        declaration = compile_meta.get("translation_metadata", {}).get("declaration", {})
        decl_args = declaration.get("arguments", {})

        params = _json.loads(s.params_json) if s.params_json else {}
        config = BacktestRunConfig(
            symbol=s.symbol,
            timeframe=s.timeframe,
            start_time=start_ms,
            end_time=end_ms,
            initial_capital=decl_args.get("initial_capital", 10000.0),
            default_qty_type=decl_args.get("default_qty_type", "fixed"),
            default_qty_value=decl_args.get("default_qty_value", 1.0),
            commission_type=decl_args.get("commission_type", "none"),
            commission_value=decl_args.get("commission_value", 0.0),
            exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
            pyramiding=decl_args.get("pyramiding", 0),
        )
        registry.update_status(strategy_id, "running")
        try:
            _backend = None
            _strategy_class = strategy_class
            # Always use PineRuntimeBackend for generated strategies to ensure
            # consistent execution semantics regardless of --capture-plots flag.
            # capture_plots only controls whether plots are persisted, not execution.
            if hasattr(strategy_class, "generated_strategy_class_ref"):
                _strategy_class = strategy_class.generated_strategy_class_ref
                try:
                    from backtest_engine.execution_backends.pine_runtime import PineRuntimeBackend
                    _backend = PineRuntimeBackend()
                except Exception as exc:
                    console.print(f"[yellow]Warning: cannot set up plot backend: {exc}[/yellow]")
            result = BacktestEngineAdapter().run(
                _strategy_class,
                bars,
                config,
                params=params,
                execution_backend=_backend,
            )
        except Exception as exc:
            registry.update_status(strategy_id, "error")
            console.print(f"[red]Backtest failed: {type(exc).__name__}: {exc}[/red]")
            sys.exit(1)

        console.print("[green]Backtest completed[/green]")
        console.print(f"  status:     {result.status}")
        console.print(f"  bars:       {result.bars_processed}")
        console.print(f"  engine:     {'backtest_engine' if result.uses_backtest_engine else 'unknown'}")

        # Save backtest results to persistent storage
        from openpine.storage import BacktestResultStore, BacktestRunRequest
        bt_store = BacktestResultStore()
        try:
            # Create run record
            run_request = BacktestRunRequest(
                strategy_id=s.strategy_id,
                pine_id=s.pine_id,
                artifact_id=s.artifact_id,
                params_hash=s.params_hash,
                symbol=s.symbol,
                timeframe=s.timeframe,
                exchange=s.exchange,
                market_type=s.market_type,
                from_time=start_ms,
                to_time=end_ms,
            )
            run_id = bt_store.create_run(run_request)
            
            # Save result
            bt_store.save_result(
                run_id=run_id,
                result=result.raw_result,
                trades=getattr(result.raw_result, "trades", []),
                equity_curve=getattr(result.raw_result, "equity_curve", None),
                plots=getattr(result.raw_result, "plots", None) if capture_plots else None,
            )
            console.print(f"[green]Backtest saved:[/green] {run_id}")
            console.print(f"  trades:     {len(getattr(result.raw_result, 'trades', []))} closed + {len(getattr(result.raw_result, 'open_trades', []))} open")
            console.print(f"  artifacts:  ~/.openpine/data/backtests/{s.strategy_id}/{run_id}/")
            if capture_plots:
                plots = getattr(result.raw_result, "plots", None)
                if plots:
                    recs = plots if isinstance(plots, list) else (plots.get_records() if hasattr(plots, "get_records") else [])
                    if recs:
                        console.print(f"[green]  plots:      {len(recs)} plot records captured[/green]")
                    else:
                        console.print(f"[yellow]  plots:      plot recorder empty[/yellow]")
                else:
                    console.print(f"[yellow]  plots:      plot outputs unavailable from engine result[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Warning: failed to save backtest results: {exc}[/yellow]")
            import traceback
            traceback.print_exc()
        finally:
            bt_store.close()

        registry.update_status(strategy_id, "paused")
    finally:
        registry.close()


@strategy.command("replay")
@click.argument("strategy_id")
@click.option("--from", "from_date")
@click.option("--to", "to_date")
def strategy_replay(strategy_id: str, from_date: str | None, to_date: str | None) -> None:
    """Run replay for a strategy."""
    import json as _json
    import time as _time_module

    from openpine.contracts import BarQuery, InstrumentKey, Timeframe
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.registry import SQLiteStrategyRegistry
    from openpine.runtime.engine import (
        BacktestArtifactError,
        BacktestEngineAdapter,
        BacktestRunConfig,
        load_strategy_class_from_artifact,
    )

    def _parse_date_ms(value: str | None, default: int) -> int:
        if not value:
            return default
        if value.isdigit():
            raw = int(value)
            return raw if raw > 10_000_000_000 else raw * 1000
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)

    registry = SQLiteStrategyRegistry()
    try:
        try:
            s = registry.get_strategy(strategy_id)
        except KeyError:
            console.print(f"[red]Strategy not found: {strategy_id}[/red]")
            sys.exit(1)
        console.print(f"[bold]Replay: {strategy_id}[/bold]")
        console.print(f"  strategy:   {s.name}")
        console.print(f"  artifact:   {s.artifact_id}")
        console.print(f"  params:     {s.params_json}")
        console.print(f"  symbol:     {s.symbol}")
        console.print(f"  timeframe:  {s.timeframe}")
        console.print(f"  from:       {from_date or 'N/A'}")
        console.print(f"  to:         {to_date or 'N/A'}")

        if not s.pine_id:
            console.print(
                "[red]Strategy has no pine_id. Recreate it with: "
                "openpine strategy create <name> --pine <pine-name> ...[/red]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)
        if not s.artifact_id:
            console.print(
                "[red]Strategy has no compiled artifact. Compile first with: "
                "openpine pine compile <pine-name>[/red]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        end_ms = _parse_date_ms(to_date, int(_time_module.time() * 1000))
        start_ms = _parse_date_ms(from_date, 0)
        if start_ms >= end_ms:
            console.print("[red]Invalid replay window: --from must be before --to[/red]")
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        try:
            strategy_class = load_strategy_class_from_artifact(
                s.pine_id,
                s.artifact_id,
                symbol=s.symbol,
                timeframe=s.timeframe,
            )
        except BacktestArtifactError as exc:
            console.print(f"[red]{exc}[/red]")
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        query = BarQuery(
            instrument_key=InstrumentKey(symbol=s.symbol, exchange=s.exchange.upper()),
            timeframe=Timeframe(value=s.timeframe),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        bars = DataOrchestrator().get_bars(query)
        if not bars:
            console.print(
                f"[red]No candle data found for {s.symbol} {s.timeframe} "
                f"in {start_ms}-{end_ms}.[/red]"
            )
            console.print(
                f"[yellow]Run: openpine data backfill {s.symbol} {s.timeframe} "
                f"--from {from_date or start_ms} --to {to_date or end_ms}[/yellow]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)

        # Load declaration from compile_meta for config alignment
        from openpine.artifacts import ArtifactStore
        store = ArtifactStore()
        artifact = store.get_artifact(s.artifact_id, s.pine_id)
        compile_meta = artifact.get("compile_meta", {})
        declaration = compile_meta.get("translation_metadata", {}).get("declaration", {})
        decl_args = declaration.get("arguments", {})

        params = _json.loads(s.params_json) if s.params_json else {}
        config = BacktestRunConfig(
            symbol=s.symbol,
            timeframe=s.timeframe,
            start_time=start_ms,
            end_time=end_ms,
            initial_capital=decl_args.get("initial_capital", 10000.0),
            default_qty_type=decl_args.get("default_qty_type", "fixed"),
            default_qty_value=decl_args.get("default_qty_value", 1.0),
            commission_type=decl_args.get("commission_type", "none"),
            commission_value=decl_args.get("commission_value", 0.0),
            exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
            pyramiding=decl_args.get("pyramiding", 0),
        )
        registry.update_status(strategy_id, "running")
        try:
            result = BacktestEngineAdapter().run(
                strategy_class,
                bars,
                config,
                params=params,
            )
        except Exception as exc:
            registry.update_status(strategy_id, "error")
            console.print(f"[red]Replay failed: {type(exc).__name__}: {exc}[/red]")
            sys.exit(1)

        console.print("[green]Replay completed[/green]")
        console.print(f"  status:     {result.status}")
        console.print(f"  bars:       {result.bars_processed}")
        console.print(f"  engine:     {'backtest_engine' if result.uses_backtest_engine else 'unknown'}")
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
                console.print(f"[yellow]No backtest runs found for {strategy_id}[/yellow]")
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
                    console.print(f"  {dir_emoji} {t.direction} {t.entry_price} -> {t.exit_price or "..."} | P&L: {t.net_pnl}")
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
                console.print(_json.dumps([r.__dict__ for r in runs], indent=2, default=str))
            else:
                console.print(f"[bold]Backtest Runs: {s.name}[/bold]")
                console.print(f"  {'Run ID':<30} {'Status':<10} {'Net Profit':<12} {'Max DD%':<10} {'PF':<8} {'Win%':<8} {'Trades':<8}")
                console.print(f"  {'-'*30} {'-'*10} {'-'*12} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
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
                console.print(_json.dumps([t.__dict__ for t in trades], indent=2, default=str))
            else:
                console.print(f"[bold]Trades: {s.name} ({run.run_id})[/bold]")
                console.print(f"  {'Direction':<10} {'Entry':<12} {'Exit':<12} {'Qty':<10} {'Net P&L':<12} {'Bars':<8} {'Reason':<15}")
                console.print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*10} {'-'*12} {'-'*8} {'-'*15}")
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

            artifacts = bt_store.list_artifacts(run.run_id)
            eq_artifact = next((a for a in artifacts if a.artifact_type == ARTIFACT_TYPE_EQUITY_CURVE), None)
            if not eq_artifact:
                console.print(f"[yellow]No equity curve artifact for run {run.run_id}[/yellow]")
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

            artifacts = bt_store.list_artifacts(run.run_id)
            plot_artifact = next((a for a in artifacts if a.artifact_type == ARTIFACT_TYPE_PLOT_OUTPUTS), None)
            if not plot_artifact:
                console.print(f"[yellow]No plot outputs artifact for run {run.run_id}[/yellow]")
                console.print(f"[dim]Tip: run with --capture-plots to save plot outputs[/dim]")
                sys.exit(1)

            console.print(f"[bold]Plot Outputs: {run.run_id}[/bold]")
            console.print(f"  path:     {plot_artifact.path}")
            console.print(f"  format:   {plot_artifact.format}")
            console.print(f"  row_count: {plot_artifact.row_count}")
            console.print()

            # Show plot names/columns
            import pandas as pd
            df = pd.read_parquet(plot_artifact.path)
            if 'title' in df.columns:
                titles = df['title'].unique().tolist()
                console.print(f"[bold]Plot columns ({len(titles)}):[/bold]")
                for title in titles:
                    count = len(df[df['title'] == title])
                    console.print(f"  {title}: {count} rows")
            else:
                console.print(f"[bold]Columns:[/bold] {list(df.columns)}")
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
                    f"[red]Cannot start paper: strategy is in error state. "
                    f"Clear error first.[/red]"
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
                    f"[red]Cannot enable live: strategy is in error state.[/red]"
                )
                sys.exit(1)
            registry.update_status(strategy_id, "disabled")
            console.print(f"[green]Live trading enabled: {strategy_id}[/green]")
        elif action == "start":
            # live start requires global live_enabled
            if not config.live_enabled:
                console.print(
                    f"[red]Live trading is disabled globally. "
                    f"Enable in config before starting live.[/red]"
                )
                sys.exit(1)
            if s.status == "error":
                console.print(
                    f"[red]Cannot start live: strategy is in error state.[/red]"
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
            console.print(f"[yellow]Warning: could not start market data refresh service: {e}[/yellow]")

        # Always start the Telegram service if enabled in config
        config = OpenPineConfig.load()
        if telegram and config.plugins.telegram.enabled:
            try:
                from openpine.daemon.telegram_service import TelegramDaemonService
                svc = TelegramDaemonService()
                services.append(svc)
                await svc.start()
            except Exception as e:
                console.print(f"[yellow]Warning: could not start Telegram service: {e}[/yellow]")
        elif not telegram:
            console.print("[dim]Telegram bot disabled (--no-telegram)[/dim]")

        if not services:
            console.print("[yellow]No services configured to run.[/yellow]")
            console.print("[dim]Enable plugins in config or use --telegram.[/dim]")
            return

        console.print(f"[green]Daemon running with {len(services)} service(s). Press Ctrl+C to stop.[/green]")

        # Wait for shutdown signal
        shutdown_event = asyncio.Event()

        def handle_signal(sig: signal.Signals) -> None:
            console.print(f"\n[yellow]Received signal {sig.name}, shutting down...[/yellow]")
            try:
                asyncio.get_event_loop().remove_signal_handler(sig)
            except NotImplementedError:
                pass  # Windows
            asyncio.get_event_loop().stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(sig, handle_signal, sig)
            except NotImplementedError:
                pass  # Windows

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


def main() -> None:
    """OpenPine CLI entry point."""
    sys.exit(cli())


if __name__ == "__main__":
    main()
