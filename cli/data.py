"""Data management CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone

import click
from rich.console import Console

from openpine.jobs import Job, JobScheduler, JobStatus, JobType

console = Console()
_cli_scheduler = JobScheduler()


def _fmt_utc_ms_as(timestamp_ms: int, fmt: str) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).strftime(fmt)


def _parse_cli_ymd_ms(value: str, *, option_name: str) -> tuple[int | None, str | None]:
    try:
        return int(datetime.strptime(value, "%Y-%m-%d").timestamp() * 1000), None
    except ValueError:
        return None, f"Invalid {option_name} date format: {value} (use YYYY-MM-DD)"


def _parse_data_backfill_window(
    *,
    from_date: str,
    to_date: str | None,
    now_ms: int,
) -> tuple[int | None, int | None, str | None]:
    start_ms, error = _parse_cli_ymd_ms(from_date, option_name="--from")
    if error:
        return None, None, error
    assert start_ms is not None
    if to_date:
        end_ms, error = _parse_cli_ymd_ms(to_date, option_name="--to")
        if error:
            return None, None, error
        assert end_ms is not None
    else:
        end_ms = now_ms
    return start_ms, end_ms, None


def _run_sync_marketdata_backfill(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    market: str,
    start_ms: int,
    end_ms: int,
    timeout: int,
    console,
) -> bool:
    import signal

    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataCoverageError, DataOrchestrator
    from openpine.data.provider_adapter import create_local_marketdata_provider_adapter

    class BackfillTimeoutError(TimeoutError):
        pass

    def _raise_timeout(signum, frame):
        raise BackfillTimeoutError

    console.print("[dim]Fetching candles through marketdata-provider...[/dim]")
    query = BarQuery(
        instrument=InstrumentKey(exchange=exchange.lower(), market=market.lower(), symbol=symbol.upper()),
        timeframe=parse_timeframe(timeframe),
        start_ms=start_ms,
        end_ms=end_ms,
        source="auto",
        gap_policy="fail",
    )

    previous_handler = None
    try:
        previous_handler = signal.signal(signal.SIGALRM, _raise_timeout) if timeout > 0 else None
        if timeout > 0:
            signal.setitimer(signal.ITIMER_REAL, timeout)
        provider = create_local_marketdata_provider_adapter()
        series = DataOrchestrator(provider=provider).load_bars(query)
    except BackfillTimeoutError:
        console.print(f"[red]Backfill timed out after {timeout}s[/red]")
        return False
    except DataCoverageError as exc:
        console.print(f"[red]Backfill failed:[/red] {exc}")
        return False
    except Exception as exc:
        console.print(f"[red]Backfill failed:[/red] {exc}")
        return False
    finally:
        if timeout > 0:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if previous_handler is not None:
                signal.signal(signal.SIGALRM, previous_handler)

    if not series.bars:
        console.print("[yellow]No candles fetched[/yellow]")
        return False

    console.print(f"[green]Backfill complete: {len(series.bars)} candles available[/green]")
    return True


@click.group()
def data() -> None:
    """Data management commands."""
    pass


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
                    _fmt_utc_ms_as(updated_at, "%Y-%m-%d %H:%M") if updated_at else "N/A"
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
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataOrchestrator, StorageUnavailableError

    console.print(f"[bold]Data gaps[/bold] {symbol} {timeframe} (exchange={exchange}, market={market})")

    query = BarQuery(
        instrument=InstrumentKey(exchange=exchange, symbol=symbol, market=market),
        timeframe=parse_timeframe(timeframe),
        start_ms=0,
        end_ms=int(2**63 - 1),
        source="storage",
        gap_policy="allow_with_metadata",
    )
    try:
        gaps = DataOrchestrator().detect_gaps(query)
    except StorageUnavailableError as exc:
        raise click.ClickException(f"storage gap scan failed: {exc}") from exc

    if not gaps:
        console.print("[green]No gaps found[/green]")
        return

    console.print(f"[yellow]{len(gaps)} gap(s) found:[/yellow]")
    for gap in gaps:
        console.print(
            f"  gap: {_fmt_utc_ms_as(gap.gap_start, '%Y-%m-%d %H:%M')}"
            f" → {_fmt_utc_ms_as(gap.gap_end, '%Y-%m-%d %H:%M')}"
            f"  ({(gap.gap_end - gap.gap_start) / 1000 / 3600:.1f}h missing)"
        )


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
@click.option("--wait", is_flag=True, help="Wait for backfill to complete synchronously")
@click.option("--timeout", default=600, help="Timeout in seconds for --wait mode")
def data_backfill(
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str | None,
    exchange: str,
    market: str,
    price_type: str,
    wait: bool,
    timeout: int,
) -> None:
    """Trigger backfill for a symbol/timeframe."""

    console.print(f"[bold]Data backfill[/bold] {symbol} {timeframe} exchange={exchange}")
    console.print(f"  from: {from_date}  to: {to_date or 'today'}")

    start_ms, end_ms, error = _parse_data_backfill_window(
        from_date=from_date,
        to_date=to_date,
        now_ms=int(datetime.now().timestamp() * 1000),
    )
    if error:
        console.print(f"[red]{error}[/red]")
        return
    assert start_ms is not None
    assert end_ms is not None

    if wait:
        _run_sync_marketdata_backfill(
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            market=market,
            start_ms=start_ms,
            end_ms=end_ms,
            timeout=timeout,
            console=console,
        )
        return

    # Async path: enqueue a backfill job
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
    console.print(f"[dim]Use --wait to fetch synchronously, or check:[/dim]")
    console.print(f"  openpine jobs show {job.id[:8]}")


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

    console.print(f"[bold]Data inspect[/bold] {symbol} {timeframe}")

    try:
        start_ms = int(dt.strptime(from_date, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        console.print(f"[red]Invalid --from date format: {from_date} (use YYYY-MM-DD)[/red]")
        return

    end_ms = int(
        dt.strptime(to_date, "%Y-%m-%d").timestamp() * 1000
    ) if to_date else int(dt.now().timestamp() * 1000)

    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataOrchestrator, DataCoverageError

    query = BarQuery(
        instrument=InstrumentKey(exchange=exchange, market=market, symbol=symbol),
        timeframe=parse_timeframe(timeframe),
        start_ms=start_ms,
        end_ms=end_ms,
        source="storage",
        gap_policy="allow_with_metadata",
    )
    try:
        series = DataOrchestrator().load_bars(query)
    except DataCoverageError as exc:
        console.print(f"[red]Data read failed:[/red] {exc}")
        return
    bars = list(series.bars)

    if not bars:
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

    for bar in bars[:20]:
        ot = datetime.fromtimestamp(bar.time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        tbl.add_row(
            ot,
            str(round(bar.open, 6)),
            str(round(bar.high, 6)),
            str(round(bar.low, 6)),
            str(round(bar.close, 6)),
            "" if bar.volume is None else str(round(bar.volume, 2)),
        )
    console.print(tbl)
    console.print(
        "[dim]"
        f"Canonical bars: {len(bars)} | Coverage: {series.coverage.status} | "
        f"Missing intervals: {len(series.coverage.missing_intervals)}"
        "[/dim]"
    )


@data.command("doctor")
@click.argument("symbol", required=True)
@click.argument("timeframe", required=True)
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD, default: today)")
@click.option("--exchange", default="binance", help="Exchange name")
@click.option("--market", default="spot", help="Market type")
@click.option("--price-type", "price_type", default="trade", help="Price type")
def data_doctor(
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str | None,
    exchange: str,
    market: str,
    price_type: str,
) -> None:
    """Run diagnostic checks on candle data."""
    from datetime import datetime as dt
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataCoverageError, DataOrchestrator

    console.print(f"[bold]Data doctor[/bold] {symbol} {timeframe}")

    try:
        start_ms = int(dt.strptime(from_date, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        console.print(f"[red]Invalid --from date format: {from_date}[/red]")
        return

    end_ms = int(dt.strptime(to_date, "%Y-%m-%d").timestamp() * 1000) if to_date else int(dt.now().timestamp() * 1000)

    query = BarQuery(
        instrument=InstrumentKey(exchange=exchange, market=market, symbol=symbol),
        timeframe=parse_timeframe(timeframe),
        start_ms=start_ms,
        end_ms=end_ms,
        source="storage",
        gap_policy="allow_with_metadata",
    )
    try:
        series = DataOrchestrator().load_bars(query)
    except DataCoverageError as exc:
        console.print(f"[red]Data doctor failed:[/red] {exc}")
        return
    bars = list(series.bars)
    coverage = series.coverage

    # Classification
    if len(bars) == 0:
        classification = "NO_DATA"
    elif coverage.duplicate_timestamps:
        classification = "DUPLICATE_TIMESTAMPS"
    elif coverage.missing_intervals:
        classification = "COVERAGE_GAP"
    elif coverage.status != "valid":
        classification = coverage.status.upper()
    else:
        classification = "DATA_OK"

    # Output report
    console.print(f"\n[bold]Diagnostic Report[/bold]")
    console.print(f"  Classification: [bold]{classification}[/bold]")
    console.print(f"  Canonical bars: {len(bars)}")
    console.print(f"  Coverage status: {coverage.status}")
    console.print(f"  Missing intervals: {len(coverage.missing_intervals)}")
    console.print(f"  Duplicate timestamps: {len(coverage.duplicate_timestamps)}")
    console.print(f"  Time range: {from_date} → {to_date or 'today'}")

    if classification == "DUPLICATE_TIMESTAMPS":
        console.print("[red]  Critical: duplicate timestamps found in canonical coverage.[/red]")
    elif classification == "COVERAGE_GAP":
        console.print("[yellow]  Warning: requested window has coverage gaps.[/yellow]")
    elif classification == "DATA_OK":
        console.print("[green]  Data coverage is valid.[/green]")


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
