"""Account and data routes."""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    AccountResponse,
    CacheStatusResponse,
    DataBackfillRequest,
    DataCoverageResponse,
    KillSwitchRequest,
    RiskStatusResponse,
)
from openpine.gateway.ws_manager import ws_manager
from openpine.jobs import JobStatus
from openpine.data.persistent_cache import default_cache_dir

log = structlog.get_logger(__name__)
router = APIRouter(tags=["accounts-data-risk"])


# ── Accounts ──────────────────────────────────────────────────────────────────


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(
    state: GatewayState = Depends(get_state),
) -> list[AccountResponse]:
    """List all accounts."""
    rows = state.storage.execute(
        "SELECT account_id, name, exchange, market_type, mode, live_enabled, created_at FROM accounts ORDER BY created_at DESC"
    ).fetchall()
    return [
        AccountResponse(
            account_id=r[0],
            name=r[1],
            exchange=r[2] or "",
            market_type=r[3] or "spot",
            mode=r[4] or "paper",
            live_enabled=bool(r[5]),
            created_at=r[6] or 0,
        )
        for r in rows
    ]


# ── Data ──────────────────────────────────────────────────────────────────────


@router.get("/data/cache", response_model=CacheStatusResponse)
async def data_cache_status(
    state: GatewayState = Depends(get_state),
) -> CacheStatusResponse:
    """Get cache status."""
    summary = _data_summary(state)
    instruments = {item["symbol"] for item in summary["series"]}
    timeframes = {item["timeframe"] for item in summary["series"]}

    return CacheStatusResponse(
        cache_dir=str(default_cache_dir()),
        total_size_bytes=int(summary["cache_size_bytes"]),
        instruments=sorted(instruments),
        timeframes=sorted(timeframes),
    )


@router.get("/data/summary")
async def data_summary(
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Return market-data and order inventory for dashboard/data page."""
    return _data_summary(state)


@router.post("/data/series/{series_id}/refresh")
async def refresh_data_series(
    series_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Refresh a market-data series by fetching bars after the newest cached bar."""
    series = _series_by_id(state).get(series_id)
    if series is None:
        raise HTTPException(404, f"Data series not found: {series_id}")
    if not series.get("latest_ms"):
        raise HTTPException(400, "Series has no latest bar to refresh from")

    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    tf = parse_timeframe(str(series["timeframe"]))
    duration_ms = tf.duration_ms or 60_000
    now_ms = int(time.time() * 1000)
    start_ms = int(series.get("earliest_ms") or series["latest_ms"])
    before_ranges = len(series.get("ranges") or [])
    if int(series["latest_ms"]) + duration_ms >= now_ms and series.get("status") == "actual":
        return {
            "status": "actual",
            "bars_loaded": 0,
            "from_ms": start_ms,
            "to_ms": now_ms,
            "latest_ms": series.get("latest_ms"),
            "coverage_ranges_before": before_ranges,
            "coverage_ranges_after": before_ranges,
            "message": "Series already actual",
            "series": series,
        }

    query = BarQuery(
        instrument=InstrumentKey(
            exchange=str(series["exchange"]),
            market=str(series["market_type"]),
            symbol=str(series["symbol"]),
        ),
        timeframe=tf,
        start_ms=start_ms,
        end_ms=now_ms,
        source="auto",
        gap_policy="allow_with_metadata",
    )
    loaded = state.orchestrator.load_bars(query)
    refreshed = _series_by_id(state).get(series_id) or series
    return {
        "status": "refreshed",
        "bars_loaded": len(loaded.bars),
        "coverage_complete": bool(getattr(loaded.coverage, "is_complete", False)),
        "from_ms": start_ms,
        "to_ms": now_ms,
        "latest_ms": refreshed.get("latest_ms"),
        "coverage_ranges_before": before_ranges,
        "coverage_ranges_after": len(refreshed.get("ranges") or []),
        "message": f"Loaded {len(loaded.bars):,} bars",
    }


@router.delete("/data/series/{series_id}")
async def delete_data_series(
    series_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Delete cached candle data for one exchange/market/symbol/timeframe group."""
    series = _series_by_id(state).get(series_id)
    if series is None:
        raise HTTPException(404, f"Data series not found: {series_id}")

    deleted_files = _delete_persistent_cache_series(series)
    deleted_marketdata = _delete_marketdata_segment_series(state, series)
    deleted_manifests = _delete_candle_manifest_series(state, series)
    return {
        "status": "deleted",
        "series_id": series_id,
        "files": deleted_files,
        "marketdata_files": deleted_marketdata,
        "manifests": deleted_manifests,
    }


@router.delete("/data/orders")
async def delete_data_orders(
    symbol: str | None = None,
    strategy_id: str | None = None,
    status: str | None = None,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Delete execution orders, optionally filtered by symbol/strategy/status."""
    where: list[str] = []
    params: list[object] = []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if strategy_id:
        where.append("strategy_id = ?")
        params.append(strategy_id)
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_ids = [row[0] for row in state.storage.execute(f"SELECT order_id FROM orders {where_sql}", tuple(params)).fetchall()]
    if not order_ids:
        return {"status": "deleted", "orders_deleted": 0, "fills_deleted": 0}

    placeholders = ",".join("?" for _ in order_ids)
    with state.storage.transaction():
        state.storage.execute(f"DELETE FROM fills WHERE order_id IN ({placeholders})", tuple(order_ids))
        fills_deleted = state.storage.execute("SELECT changes()").fetchone()[0]
        state.storage.execute(f"DELETE FROM orders WHERE order_id IN ({placeholders})", tuple(order_ids))
        orders_deleted = state.storage.execute("SELECT changes()").fetchone()[0]
    return {"status": "deleted", "orders_deleted": orders_deleted, "fills_deleted": fills_deleted}


@router.get("/data/coverage/{symbol}", response_model=list[DataCoverageResponse])
async def data_coverage(
    symbol: str,
    exchange: str = Query("binance"),
    market_type: str = Query("spot"),
    state: GatewayState = Depends(get_state),
) -> list[DataCoverageResponse]:
    """Get data coverage for a symbol."""
    try:
        from marketdata_provider import create_candle_store
        from marketdata_provider.config import MarketDataConfig, StorageConfig

        cache_dir = state.config.data_cache_root or (state.config.data_dir / "cache")
        store = create_candle_store(
            MarketDataConfig(storage=StorageConfig(cache_dir=cache_dir / "marketdata"))
        )
        coverage = store.coverage(
            instrument=symbol,
            exchange=exchange,
            market=market_type,
        )
        results = []
        for item in coverage:
            results.append(
                DataCoverageResponse(
                    symbol=symbol,
                    timeframe=item.get("timeframe", ""),
                    earliest_ms=item.get("earliest_ms"),
                    latest_ms=item.get("latest_ms"),
                    bar_count=item.get("bar_count", 0),
                    gaps=item.get("gaps", []),
                )
            )
        return results
    except Exception as exc:
        log.warning("coverage_error", symbol=symbol, error=str(exc))
        return []


@router.post("/data/backfill")
async def data_backfill(
    body: DataBackfillRequest,
    background_tasks: BackgroundTasks,
    state: GatewayState = Depends(get_state),
) -> dict[str, str]:
    """Start a data backfill job."""
    from openpine.jobs import Job, JobType

    from_ms = _parse_date_ms(body.from_time)
    to_ms = _parse_date_ms(body.to_time)
    if from_ms >= to_ms:
        raise HTTPException(400, "from_time must be before to_time")

    job = Job(
        job_type=JobType.BACKFILL,
        idempotency_key=(
            f"data-backfill:{body.exchange.lower()}:{body.market_type.lower()}:"
            f"{body.symbol.upper()}:{body.timeframe}:{from_ms}:{to_ms}"
        ),
        input={
            "symbol": body.symbol,
            "timeframe": body.timeframe,
            "from_time": from_ms,
            "to_time": to_ms,
            "exchange": body.exchange,
            "market_type": body.market_type,
        },
    )
    job = state.scheduler.enqueue(job)
    background_tasks.add_task(_run_data_backfill_job, job.id, dict(job.input or {}), state)
    ws_manager.update_progress(
        job.id,
        "data_backfill",
        job.status.value,
        0.0,
        f"Queued candle backfill for {body.symbol.upper()} {body.timeframe}",
        detail=dict(job.input or {}),
    )
    await ws_manager.broadcast_progress(job.id)
    log.info("backfill_enqueued", symbol=body.symbol, timeframe=body.timeframe)
    return {"job_id": job.id, "status": job.status.value}


async def _run_data_backfill_job(job_id: str, payload: dict[str, object], state: GatewayState) -> None:
    job = state.scheduler.get_job(job_id)
    if job is None or job.status != JobStatus.PENDING:
        return

    state.scheduler.mark_running(job_id)
    ws_manager.update_progress(
        job_id,
        "data_backfill",
        "running",
        0.02,
        "Starting candle backfill...",
        detail=payload,
    )
    await ws_manager.broadcast_progress(job_id)

    def _progress(*args: object) -> None:
        bars_done = int(args[0] or 0) if len(args) > 0 else 0
        pages_done = int(args[1] or 0) if len(args) > 1 else 0
        total_bars = int(args[2] or 0) if len(args) > 2 else 0
        total_pages = int(args[3] or 0) if len(args) > 3 else 0
        phase = str(args[5] or "fetch") if len(args) > 5 else "fetch"
        pct = min(0.98, bars_done / total_bars) if total_bars > 0 else 0.2
        detail = {
            **payload,
            "bars_processed": bars_done,
            "total_bars": total_bars,
            "pages_processed": pages_done,
            "total_pages": total_pages,
            "phase": phase,
        }
        ws_manager.update_progress(
            job_id,
            "data_backfill",
            "running",
            pct,
            f"Loading candles: {bars_done:,}/{total_bars:,} bars" if total_bars else "Loading candles...",
            detail=detail,
        )

    try:
        result = await asyncio.to_thread(_run_data_backfill_sync, payload, state, _progress)
        bars_loaded = int(result["bars_loaded"])
        result = {
            **payload,
            **result,
        }
        state.scheduler.mark_done(job_id, result)
        ws_manager.update_progress(
            job_id,
            "data_backfill",
            "done",
            1.0,
            f"Loaded {bars_loaded:,} candles, skipped {int(result.get('skipped_existing') or 0):,} existing",
            detail=result,
        )
        await ws_manager.broadcast_progress(job_id)
    except Exception as exc:
        state.scheduler.mark_failed(job_id, str(exc))
        ws_manager.update_progress(job_id, "data_backfill", "failed", 0.0, str(exc), detail=payload)
        await ws_manager.broadcast_progress(job_id)
        log.warning("backfill_failed", job_id=job_id, error=str(exc))


def _run_data_backfill_sync(payload: dict[str, object], state: GatewayState, progress_callback):
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    query = BarQuery(
        instrument=InstrumentKey(
            exchange=str(payload["exchange"]).lower(),
            market=str(payload["market_type"]).lower(),
            symbol=str(payload["symbol"]).upper(),
        ),
        timeframe=parse_timeframe(str(payload["timeframe"])),
        start_ms=int(payload["from_time"]),
        end_ms=int(payload["to_time"]),
        source="provider",
        gap_policy="allow_with_metadata",
    )
    series = state.orchestrator.load_bars(query, progress_callback=progress_callback)
    bars_loaded, skipped_existing = _store_backfill_series(state, series)
    return {
        "bars_loaded": bars_loaded,
        "skipped_existing": skipped_existing,
        "coverage_complete": bool(getattr(series.coverage, "is_complete", False)),
    }


def _store_backfill_series(state: GatewayState, series) -> tuple[int, int]:
    from marketdata_provider.contracts import BarQuery, BarSeries
    from openpine.data.orchestrator import DataOrchestrator, StorageUnavailableError

    bars_loaded = 0
    skipped_existing = 0
    for bar in series.bars:
        query = BarQuery(
            instrument=bar.instrument,
            timeframe=bar.timeframe,
            start_ms=bar.time,
            end_ms=bar.time_close,
            source="storage",
            gap_policy="allow_with_metadata",
        )
        single = BarSeries(
            query=query,
            bars=(bar,),
            coverage=DataOrchestrator.coverage_for_series(query, (bar,), "provider"),
        )
        try:
            state.orchestrator.store_bars(single)
            bars_loaded += 1
        except StorageUnavailableError as exc:
            if "conflicting closed candle" not in str(exc):
                raise
            skipped_existing += 1
    return bars_loaded, skipped_existing


def _parse_date_ms(value: str) -> int:
    if value.isdigit():
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _data_summary(state: GatewayState) -> dict[str, object]:
    series = _data_series_inventory(state)
    db_size = _database_size_bytes(state)
    cache_size = _persistent_cache_size_bytes()
    candle_store_size = _candle_store_size_bytes(state)
    orders_summary = _orders_summary(state)
    return {
        "database_size_bytes": db_size,
        "cache_size_bytes": cache_size,
        "candle_store_size_bytes": candle_store_size,
        "total_size_bytes": db_size + cache_size + candle_store_size,
        "total_bars": sum(int(item.get("bar_count") or 0) for item in series),
        "series_count": len(series),
        "series": series,
        "orders": orders_summary,
    }


def _data_series_inventory(state: GatewayState) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    _merge_persistent_cache_groups(groups)
    _merge_marketdata_segment_groups(state, groups)
    _merge_candle_manifest_groups(state, groups)
    for entry in groups.values():
        ranges = list(entry.get("ranges") or [])
        stored_rows = int(entry.get("bar_count") or 0)
        coverage_ranges = _coalesce_ranges(ranges, str(entry["timeframe"]))
        estimated_unique = _estimate_unique_bars(coverage_ranges, str(entry["timeframe"]))
        entry["stored_rows"] = stored_rows
        entry["bar_count"] = min(estimated_unique, stored_rows) if stored_rows else estimated_unique
        entry["raw_range_count"] = len(ranges)
        entry["ranges"] = _compact_ranges(coverage_ranges)
        entry["role"] = _series_role(entry)
    return sorted(groups.values(), key=lambda item: (str(item["symbol"]), str(item["timeframe"])))


def _series_role(entry: dict[str, object]) -> str:
    """Classify visible data as source exchange pulls or locally derived aggregates."""
    timeframe = str(entry.get("timeframe") or "")
    source_kinds = {str(item).lower() for item in (entry.get("source_kinds") or [])}
    sources = {str(item).lower() for item in (entry.get("sources") or [])}
    if timeframe == "1m":
        return "source"
    if any("aggregate" in item or "derived" in item for item in source_kinds | sources):
        return "derived"
    return "derived"


def _series_by_id(state: GatewayState) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in _data_series_inventory(state)}


def _merge_persistent_cache_groups(groups: dict[tuple[str, str, str, str, str], dict[str, object]]) -> None:
    for meta_path in default_cache_dir().glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            key = meta.get("key") or {}
            instrument = key.get("instrument") or {}
            group_key = (
                str(instrument.get("exchange", "binance")).lower(),
                str(instrument.get("market", "spot")).lower(),
                str(instrument.get("symbol", "")).upper(),
                "trade",
                str(key.get("timeframe", "")),
            )
            if not group_key[2] or not group_key[4]:
                continue
            entry = _series_entry(groups, group_key)
            rows = int(meta.get("rows") or 0)
            first = meta.get("first_time")
            last = meta.get("last_time")
            csv_path = meta_path.with_suffix(".csv")
            size = meta_path.stat().st_size + (csv_path.stat().st_size if csv_path.exists() else 0)
            _extend_series(entry, rows, first, last, size, "persistent_cache", meta_path.stem)
        except Exception as exc:
            log.warning("data_cache_inventory_error", path=str(meta_path), error=str(exc))


def _merge_marketdata_segment_groups(state: GatewayState, groups: dict[tuple[str, str, str, str, str], dict[str, object]]) -> None:
    root = _marketdata_store_root(state)
    index_path = root / "index.sqlite"
    if not index_path.exists():
        return
    touched: set[tuple[str, str, str, str, str]] = set()
    try:
        with sqlite3.connect(index_path) as db:
            rows = db.execute(
                """
                SELECT id, exchange, market, symbol, timeframe, start_time, end_time, rows_count, source_kind
                FROM marketdata_segments
                """
            ).fetchall()
    except Exception as exc:
        log.warning("marketdata_store_inventory_error", path=str(index_path), error=str(exc))
        return

    for segment_id, exchange, market, symbol, timeframe, start_time, end_time, rows_count, source_kind in rows:
        source_kind = str(source_kind or "trade_kline")
        price_type = "trade" if "trade" in source_kind else source_kind
        group_key = (
            str(exchange).lower(),
            str(market).lower(),
            str(symbol).upper(),
            price_type,
            str(timeframe),
        )
        entry = _series_entry(groups, group_key)
        source_kinds = set(entry.get("source_kinds") or [])
        source_kinds.add(source_kind)
        entry["source_kinds"] = sorted(source_kinds)
        _extend_series(
            entry,
            int(rows_count or 0),
            start_time,
            end_time,
            0,
            "marketdata_store",
            str(segment_id),
        )
        touched.add(group_key)

    for group_key in touched:
        entry = _series_entry(groups, group_key)
        exchange, market, symbol, _price_type, timeframe = group_key
        source_kinds = entry.get("source_kinds") or ["trade_kline"]
        size = sum(
            _dir_size(_marketdata_segment_dir(root, exchange, market, symbol, timeframe, str(source_kind)))
            for source_kind in source_kinds
        )
        entry["size_bytes"] = int(entry.get("size_bytes") or 0) + size


def _merge_candle_manifest_groups(state: GatewayState, groups: dict[tuple[str, str, str, str, str], dict[str, object]]) -> None:
    try:
        rows = state.storage.execute(
            """
            SELECT exchange, market_type, symbol, price_type, timeframe,
                   min_open_time, max_open_time, row_count, file_size_bytes, manifest_id
            FROM candle_manifests
            WHERE COALESCE(is_active, 1) = 1
            """
        ).fetchall()
    except Exception:
        return
    for row in rows:
        group_key = (
            str(row[0]).lower(),
            str(row[1]).lower(),
            str(row[2]).upper(),
            str(row[3] or "trade").lower(),
            str(row[4]),
        )
        entry = _series_entry(groups, group_key)
        _extend_series(entry, int(row[7] or 0), row[5], row[6], int(row[8] or 0), "candle_store", row[9])


def _series_entry(groups: dict[tuple[str, str, str, str, str], dict[str, object]], group_key: tuple[str, str, str, str, str]) -> dict[str, object]:
    if group_key in groups:
        return groups[group_key]
    exchange, market_type, symbol, price_type, timeframe = group_key
    entry: dict[str, object] = {
        "id": _series_id(group_key),
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "price_type": price_type,
        "timeframe": timeframe,
        "earliest_ms": None,
        "latest_ms": None,
        "bar_count": 0,
        "size_bytes": 0,
        "entry_count": 0,
        "sources": [],
        "ranges": [],
        "status": "empty",
    }
    groups[group_key] = entry
    return entry


def _extend_series(entry: dict[str, object], rows: int, first: object, last: object, size: int, source: str, source_id: str) -> None:
    first_ms = int(first) if first is not None else None
    last_ms = int(last) if last is not None else None
    entry["bar_count"] = int(entry["bar_count"]) + rows
    entry["size_bytes"] = int(entry["size_bytes"]) + size
    entry["entry_count"] = int(entry["entry_count"]) + 1
    sources = set(entry.get("sources") or [])
    sources.add(source)
    entry["sources"] = sorted(sources)
    if first_ms is not None:
        current = entry.get("earliest_ms")
        entry["earliest_ms"] = first_ms if current is None else min(int(current), first_ms)
    if last_ms is not None:
        current = entry.get("latest_ms")
        entry["latest_ms"] = last_ms if current is None else max(int(current), last_ms)
    ranges = list(entry.get("ranges") or [])
    ranges.append({"from_ms": first_ms, "to_ms": last_ms, "rows": rows, "source": source, "source_id": source_id})
    entry["ranges"] = ranges
    entry["status"] = _freshness_status(entry.get("latest_ms"), str(entry["timeframe"]))


def _compact_ranges(ranges: list[dict[str, object]], limit: int = 6) -> list[dict[str, object]]:
    ordered = sorted(ranges, key=lambda item: int(item.get("from_ms") or 0))
    if len(ordered) <= limit:
        return ordered
    return ordered[:3] + [{"collapsed": len(ordered) - 5}] + ordered[-2:]


def _coalesce_ranges(ranges: list[dict[str, object]], timeframe: str) -> list[dict[str, object]]:
    try:
        from marketdata_provider.contracts import parse_timeframe

        duration_ms = parse_timeframe(timeframe).duration_ms or 60_000
    except Exception:
        duration_ms = 60_000

    intervals: list[dict[str, object]] = []
    for item in ranges:
        first = item.get("from_ms")
        last = item.get("to_ms")
        if first is None or last is None:
            continue
        first_ms = int(first)
        last_ms = int(last)
        if last_ms < first_ms:
            continue
        intervals.append(
            {
                "from_ms": first_ms,
                "to_ms": last_ms,
                "rows": int(item.get("rows") or 0),
                "sources": {str(item.get("source") or "unknown")},
            }
        )
    intervals.sort(key=lambda item: int(item["from_ms"]))

    merged: list[dict[str, object]] = []
    for item in intervals:
        if not merged or int(item["from_ms"]) > int(merged[-1]["to_ms"]) + duration_ms:
            merged.append(item)
            continue
        merged[-1]["to_ms"] = max(int(merged[-1]["to_ms"]), int(item["to_ms"]))
        merged[-1]["rows"] = int(merged[-1].get("rows") or 0) + int(item.get("rows") or 0)
        merged_sources = set(merged[-1].get("sources") or [])
        merged_sources.update(set(item.get("sources") or []))
        merged[-1]["sources"] = merged_sources

    for item in merged:
        sources = sorted(set(item.get("sources") or []))
        item["source"] = ",".join(sources)
        item.pop("sources", None)
    return merged


def _estimate_unique_bars(ranges: list[dict[str, object]], timeframe: str) -> int:
    try:
        from marketdata_provider.contracts import parse_timeframe

        duration_ms = parse_timeframe(timeframe).duration_ms or 60_000
    except Exception:
        duration_ms = 60_000

    intervals: list[tuple[int, int]] = []
    fallback_rows = 0
    for item in ranges:
        rows = int(item.get("rows") or 0)
        first = item.get("from_ms")
        last = item.get("to_ms")
        if first is None or last is None:
            fallback_rows += rows
            continue
        first_ms = int(first)
        last_ms = int(last)
        if last_ms < first_ms:
            fallback_rows += rows
            continue
        intervals.append((first_ms, last_ms))

    if not intervals:
        return fallback_rows

    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + duration_ms:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    unique = 0
    for start, end in merged:
        unique += ((end - start) // duration_ms) + 1
    return unique + fallback_rows


def _freshness_status(latest_ms: object, timeframe: str) -> str:
    if latest_ms is None:
        return "empty"
    try:
        from marketdata_provider.contracts import parse_timeframe

        duration_ms = parse_timeframe(timeframe).duration_ms or 60_000
    except Exception:
        duration_ms = 60_000
    now_ms = int(time.time() * 1000)
    current_bar_start = now_ms - (now_ms % duration_ms)
    latest_expected = current_bar_start - duration_ms
    return "actual" if int(latest_ms) >= latest_expected else "stale"


def _series_id(group_key: tuple[str, str, str, str, str]) -> str:
    return hashlib.sha256("|".join(group_key).encode("utf-8")).hexdigest()[:16]


def _database_size_bytes(state: GatewayState) -> int:
    sqlite_path = Path(state.config.sqlite_path)
    return sum((path.stat().st_size if path.exists() else 0) for path in (sqlite_path, sqlite_path.with_suffix(sqlite_path.suffix + "-wal"), sqlite_path.with_suffix(sqlite_path.suffix + "-shm")))


def _persistent_cache_size_bytes() -> int:
    return _dir_size(default_cache_dir())


def _candle_store_size_bytes(state: GatewayState) -> int:
    return _dir_size(_marketdata_store_root(state))


def _marketdata_store_root(state: GatewayState) -> Path:
    cache_dir = state.config.data_cache_root or (state.config.data_dir / "cache")
    return cache_dir / "marketdata"


def _marketdata_segment_dir(root: Path, exchange: str, market: str, symbol: str, timeframe: str, source_kind: str) -> Path:
    safe_symbol = str(symbol).upper().replace("/", "_").replace(":", "_")
    return (
        root
        / "v1"
        / f"exchange={str(exchange).lower()}"
        / f"market={str(market).lower()}"
        / f"symbol={safe_symbol}"
        / f"source={source_kind}"
        / f"timeframe={timeframe}"
    )


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def _orders_summary(state: GatewayState) -> dict[str, object]:
    total, min_ts, max_ts = state.storage.execute("SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM orders").fetchone()
    by_symbol = [
        {"symbol": row[0], "count": row[1], "latest_ms": row[2]}
        for row in state.storage.execute(
            "SELECT symbol, COUNT(*), MAX(created_at) FROM orders GROUP BY symbol ORDER BY COUNT(*) DESC"
        ).fetchall()
    ]
    by_strategy = [
        {
            "symbol": row[0],
            "strategy_id": row[1],
            "strategy_name": row[2] or row[1] or "Unknown strategy",
            "status": row[3],
            "count": row[4],
            "latest_ms": row[5],
        }
        for row in state.storage.execute(
            """
            SELECT o.symbol, o.strategy_id, s.name, o.status, COUNT(*), MAX(o.created_at)
            FROM orders o
            LEFT JOIN strategy_instances s ON s.strategy_id = o.strategy_id
            GROUP BY o.symbol, o.strategy_id, s.name, o.status
            ORDER BY MAX(o.created_at) DESC
            """
        ).fetchall()
    ]
    return {"total": total or 0, "earliest_ms": min_ts, "latest_ms": max_ts, "by_symbol": by_symbol, "by_strategy": by_strategy}


def _delete_persistent_cache_series(series: dict[str, object]) -> int:
    deleted = 0
    trash_dir = Path.cwd() / ".openpine" / "trash" / f"data-cache-{int(time.time() * 1000)}"
    for meta_path in default_cache_dir().glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            key = meta.get("key") or {}
            instrument = key.get("instrument") or {}
            if (
                str(instrument.get("exchange", "")).lower() == str(series["exchange"])
                and str(instrument.get("market", "")).lower() == str(series["market_type"])
                and str(instrument.get("symbol", "")).upper() == str(series["symbol"])
                and str(key.get("timeframe", "")) == str(series["timeframe"])
            ):
                trash_dir.mkdir(parents=True, exist_ok=True)
                for path in (meta_path, meta_path.with_suffix(".csv")):
                    if path.exists():
                        shutil.move(str(path), str(trash_dir / path.name))
                        deleted += 1
        except Exception as exc:
            log.warning("data_cache_delete_error", path=str(meta_path), error=str(exc))
    return deleted


def _delete_marketdata_segment_series(state: GatewayState, series: dict[str, object]) -> int:
    root = _marketdata_store_root(state)
    index_path = root / "index.sqlite"
    exchange = str(series["exchange"]).lower()
    market = str(series["market_type"]).lower()
    symbol = str(series["symbol"]).upper()
    timeframe = str(series["timeframe"])
    source_kinds = series.get("source_kinds") or ["trade_kline"]
    deleted = 0

    if index_path.exists():
        try:
            with sqlite3.connect(index_path) as db:
                db.execute(
                    """
                    DELETE FROM marketdata_segments
                    WHERE lower(exchange) = ? AND lower(market) = ? AND upper(symbol) = ? AND timeframe = ?
                    """,
                    (exchange, market, symbol, timeframe),
                )
                deleted += db.total_changes
        except Exception as exc:
            log.warning("marketdata_store_delete_index_error", path=str(index_path), error=str(exc))

    trash_dir = Path.cwd() / ".openpine" / "trash" / f"marketdata-store-{int(time.time() * 1000)}"
    for source_kind in source_kinds:
        path = _marketdata_segment_dir(root, exchange, market, symbol, timeframe, str(source_kind))
        if not path.exists():
            continue
        trash_dir.mkdir(parents=True, exist_ok=True)
        target = trash_dir / path.name
        if target.exists():
            target = trash_dir / f"{path.name}-{int(time.time() * 1000)}"
        shutil.move(str(path), str(target))
        deleted += 1
    return deleted


def _delete_candle_manifest_series(state: GatewayState, series: dict[str, object]) -> int:
    rows = state.storage.execute(
        """
        SELECT manifest_id, partition_path FROM candle_manifests
        WHERE exchange = ? AND market_type = ? AND symbol = ? AND price_type = ? AND timeframe = ?
        """,
        (series["exchange"], series["market_type"], series["symbol"], series["price_type"], series["timeframe"]),
    ).fetchall()
    if not rows:
        return 0
    trash_dir = Path.cwd() / ".openpine" / "trash" / f"candle-store-{int(time.time() * 1000)}"
    with state.storage.transaction():
        for manifest_id, partition_path in rows:
            if partition_path:
                path = Path(partition_path)
                if path.exists():
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(trash_dir / path.name))
            state.storage.execute("DELETE FROM candle_manifests WHERE manifest_id = ?", (manifest_id,))
    return len(rows)


# ── Risk ──────────────────────────────────────────────────────────────────────


@router.get("/risk", response_model=RiskStatusResponse)
async def risk_status(
    state: GatewayState = Depends(get_state),
) -> RiskStatusResponse:
    """Get risk manager status."""
    return RiskStatusResponse(
        kill_switch=state._risk_kill_switch[0],
        rules=["KillSwitchRule", "MaxPositionSizeRule"],
    )


@router.post("/risk/kill-switch")
async def toggle_kill_switch(
    body: KillSwitchRequest,
    state: GatewayState = Depends(get_state),
) -> dict[str, bool]:
    """Toggle global kill switch."""
    state._risk_kill_switch[0] = body.enabled
    log.warning("kill_switch_toggled", enabled=body.enabled)
    return {"kill_switch": body.enabled}
