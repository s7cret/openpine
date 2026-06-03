"""Account and data routes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    AccountResponse,
    CacheStatusResponse,
    DataBackfillRequest,
    DataCoverageResponse,
    KillSwitchRequest,
    RiskStatusResponse,
)

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
    cache_dir = state.config.data_cache_root or (state.config.data_dir / "cache")
    total_size = 0
    instruments: set[str] = set()
    timeframes: set[str] = set()

    marketdata_dir = cache_dir / "marketdata"
    if marketdata_dir.exists():
        for f in marketdata_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                # Extract instrument/TF from path conventions
                parts = f.relative_to(marketdata_dir).parts
                if len(parts) >= 1:
                    instruments.add(parts[0])
                if len(parts) >= 2:
                    timeframes.add(parts[1])

    return CacheStatusResponse(
        cache_dir=str(cache_dir),
        total_size_bytes=total_size,
        instruments=sorted(instruments),
        timeframes=sorted(timeframes),
    )


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
    state: GatewayState = Depends(get_state),
) -> dict[str, str]:
    """Start a data backfill job."""
    from openpine.jobs import Job, JobType

    job = Job(
        job_type=JobType.BACKFILL,
        input={
            "symbol": body.symbol,
            "timeframe": body.timeframe,
            "from_time": body.from_time,
            "to_time": body.to_time,
            "exchange": body.exchange,
            "market_type": body.market_type,
        },
    )
    state.scheduler.enqueue(job)
    log.info("backfill_enqueued", symbol=body.symbol, timeframe=body.timeframe)
    return {"job_id": job.id, "status": "queued"}


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
