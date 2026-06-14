"""Account and data routes."""

from __future__ import annotations

import hashlib
import asyncio
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import cast

from openpine._compat import structlog
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
from openpine.timezones import parse_timestamp_ms
from openpine.jobs import JobStatus
from openpine.data.persistent_cache import default_cache_dir
from openpine.exchange_metadata import marketdata_exchange_payloads
from marketdata_provider import search_symbols
from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe
from marketdata_provider.errors import MarketDataError

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


_STRATEGY_MARKET_TYPE_ENABLED = {'spot', 'margin', 'futures', 'delivery'}
_MARKET_TYPE_ORDER = {'spot': 0, 'margin': 1, 'futures': 2, 'delivery': 3, 'options': 4}
_MARKET_TYPE_ALIASES = {
    'spot': ['spot'],
    'margin': ['margin'],
    'futures': ['futures', 'usdm', 'linear', 'usdt_futures'],
    'delivery': ['delivery', 'coinm', 'inverse', 'coin_futures', 'delivery_futures'],
    'options': ['options'],
}
_MARKET_TYPE_LABELS = {
    'spot': 'Spot',
    'margin': 'Margin',
    'futures': 'USDT/USDC-margined futures/perpetuals',
    'delivery': 'Coin-margined/delivery futures',
    'options': 'Options',
}
_MARKET_TYPE_DESCRIPTIONS = {
    'spot': 'Immediate settlement spot markets and OHLCV feeds.',
    'margin': 'Spot-margin markets; public candles usually mirror spot feeds.',
    'futures': 'Linear stablecoin-margined perpetuals/futures.',
    'delivery': 'Inverse coin-margined perpetuals or dated futures contracts.',
    'options': 'Listed crypto options market data.',
}
_MARKET_TYPE_MAP = {
    'spot': 'spot',
    'margin': 'margin',
    'usdm': 'futures',
    'linear': 'futures',
    'usdt_futures': 'futures',
    'coinm': 'delivery',
    'inverse': 'delivery',
    'coin_futures': 'delivery',
    'delivery_futures': 'delivery',
    'options': 'options',
}
_DATA_KLINES_MAX_BARS = 200000
_DATA_KLINES_MAX_VARIABLE_WINDOW_MS = 10 * 366 * 24 * 60 * 60 * 1000
_SYMBOL_SEARCH_TIMEOUT_SECONDS = 8.0
_SYMBOL_SEARCH_RESPONSE_TIMEOUT_SECONDS = 9.0
_DATA_LOAD_RESPONSE_TIMEOUT_SECONDS = 15.0


def _strategy_market_type_payload(market_type_id: str) -> dict[str, object]:
    return {
        'id': market_type_id,
        'label': _MARKET_TYPE_LABELS[market_type_id],
        'aliases': _MARKET_TYPE_ALIASES[market_type_id],
        'description': _MARKET_TYPE_DESCRIPTIONS[market_type_id],
        'enabled_for_strategy_create': market_type_id in _STRATEGY_MARKET_TYPE_ENABLED,
    }


def _ui_market_type_ids(listed_market_types: list[str] | tuple[str, ...]) -> list[str]:
    ids = {_MARKET_TYPE_MAP[item] for item in listed_market_types if item in _MARKET_TYPE_MAP}
    return sorted(ids, key=lambda item: _MARKET_TYPE_ORDER[item])


def _exchange_enabled(exchange: dict[str, object]) -> bool:
    return bool(exchange.get('native_adapter'))


def _exchange_disabled_reason(exchange: dict[str, object]) -> str | None:
    if _exchange_enabled(exchange):
        return None
    return 'planned_provider'


def _market_metadata_payload() -> dict[str, object]:
    market_types = [_strategy_market_type_payload(item) for item in _MARKET_TYPE_ORDER]
    exchanges = []
    for exchange in marketdata_exchange_payloads():
        native_market_types = cast(
            tuple[str, ...], exchange.get('native_markets') or exchange['listed_market_types']
        )
        market_type_ids = _ui_market_type_ids(native_market_types)
        openpine_enabled = _exchange_enabled(exchange)
        exchanges.append({
            **exchange,
            'openpine_enabled': openpine_enabled,
            'symbol_search_supported': openpine_enabled,
            'disabled_reason': _exchange_disabled_reason(exchange),
            'market_types': [_strategy_market_type_payload(item) for item in market_type_ids],
        })
    return {
        'exchanges': exchanges,
        'market_types': market_types,
        'source': 'marketdata_provider.exchanges.registry',
    }


@router.get('/data/metadata')
async def data_metadata() -> dict[str, object]:
    return _market_metadata_payload()


@router.get('/data/symbols')
async def data_symbols(
    exchange: str = Query(..., min_length=1),
    market_type: str = Query('spot'),
    query: str = Query('', max_length=64),
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    exchange_id, exchange_payload = _require_enabled_exchange_payload(exchange)
    market_id = _require_enabled_market_type(exchange_payload, market_type)

    stable_quote_assets = tuple(state.config.marketdata_stable_quote_assets)
    stable_quotes_only = bool(state.config.marketdata_stable_quotes_only)
    limit = int(state.config.marketdata_symbol_search_limit)
    try:
        symbols = await asyncio.wait_for(
            asyncio.to_thread(
                search_symbols,
                exchange=exchange_id,
                market=market_id,
                query=query,
                stable_quotes_only=stable_quotes_only,
                stable_quote_assets=stable_quote_assets,
                limit=limit,
                timeout=_SYMBOL_SEARCH_TIMEOUT_SECONDS,
            ),
            timeout=_SYMBOL_SEARCH_RESPONSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail='Symbol discovery timed out') from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {
        'exchange': exchange_id,
        'market_type': market_id,
        'query': query,
        'stable_quotes_only': stable_quotes_only,
        'stable_quote_assets': list(stable_quote_assets),
        'symbols': [item.to_dict() for item in symbols],
    }


def _require_enabled_exchange_payload(exchange: str) -> tuple[str, dict[str, object]]:
    metadata = _market_metadata_payload()
    exchanges = {str(item['id']): item for item in metadata['exchanges']}  # type: ignore[index]
    exchange_id = exchange.strip().lower()
    exchange_payload = exchanges.get(exchange_id)
    if exchange_payload is None:
        raise HTTPException(status_code=404, detail=f"unknown exchange: {exchange}")
    if not exchange_payload.get('openpine_enabled'):
        raise HTTPException(
            status_code=400,
            detail=str(exchange_payload.get('disabled_reason') or 'exchange_disabled'),
        )
    return exchange_id, exchange_payload


def _require_enabled_exchange(exchange: str) -> str:
    exchange_id, _exchange_payload = _require_enabled_exchange_payload(exchange)
    return exchange_id


def _require_enabled_market_type(exchange_payload: dict[str, object], market_type: str) -> str:
    market_id = market_type.strip().lower()
    market_payloads = cast(list[dict[str, object]], exchange_payload.get('market_types') or [])
    supported = {str(item.get('id')) for item in market_payloads}
    if market_id not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported market_type for exchange: {market_type}",
        )
    for item in market_payloads:
        if item.get('id') == market_id and not item.get('enabled_for_strategy_create', False):
            raise HTTPException(
                status_code=400,
                detail=f"market_type disabled for strategy/runtime: {market_type}",
            )
    return market_id


def _bar_payload(bar: Bar) -> dict[str, object]:
    return {
        'time': bar.time,
        'time_close': bar.time_close,
        'open': bar.open,
        'high': bar.high,
        'low': bar.low,
        'close': bar.close,
        'volume': bar.volume,
    }


def _load_market_bars(
    state: GatewayState,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    max_bars: int = _DATA_KLINES_MAX_BARS,
):
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail='end_time must be greater than start_time')
    exchange_id, exchange_payload = _require_enabled_exchange_payload(exchange)
    market_id = _require_enabled_market_type(exchange_payload, market_type)
    symbol_id = symbol.strip().upper()
    if not symbol_id:
        raise HTTPException(status_code=400, detail='symbol is required')
    try:
        timeframe = parse_timeframe(interval)
        if timeframe.duration_ms is not None:
            requested_bars = (
                (int(end_time) - int(start_time) + timeframe.duration_ms - 1)
                // timeframe.duration_ms
            )
            if requested_bars > max_bars:
                raise HTTPException(
                    status_code=400,
                    detail=f'request window exceeds max bars: {max_bars}',
                )
        elif int(end_time) - int(start_time) > _DATA_KLINES_MAX_VARIABLE_WINDOW_MS:
            raise HTTPException(status_code=400, detail='request window too large')
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=exchange_id,
                market=market_id,
                symbol=symbol_id,
            ),
            timeframe=timeframe,
            start_ms=int(start_time),
            end_ms=int(end_time),
            source='auto',
            gap_policy='allow_with_metadata',
        )
        series = state.orchestrator.load_bars(query)
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return exchange_id, symbol_id, query, series.bars


@router.get('/data/klines')
async def data_klines(
    symbol: str,
    start_time: int,
    end_time: int,
    exchange: str = 'binance',
    market_type: str = 'spot',
    interval: str = '15m',
    limit: int = Query(5000, ge=1, le=_DATA_KLINES_MAX_BARS),
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    try:
        exchange_id, symbol_id, query, bars = await asyncio.wait_for(
            asyncio.to_thread(
                _load_market_bars,
                state,
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                start_time=start_time,
                end_time=end_time,
                max_bars=int(limit),
            ),
            timeout=_DATA_LOAD_RESPONSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail='Market data load timed out') from exc
    bounded_bars = list(bars)[: int(limit)]
    return {
        'exchange': exchange_id,
        'market_type': query.instrument.market,
        'symbol': symbol_id,
        'interval': query.timeframe.canonical,
        'start_time': query.start_ms,
        'end_time': query.end_ms,
        'bars': [_bar_payload(bar) for bar in bounded_bars],
    }


@router.get('/data/ticker24h')
async def data_ticker24h(
    symbol: str,
    exchange: str = 'binance',
    market_type: str = 'spot',
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    end_time = int(time.time() * 1000)
    start_time = end_time - 24 * 60 * 60 * 1000
    try:
        exchange_id, symbol_id, query, bars = await asyncio.wait_for(
            asyncio.to_thread(
                _load_market_bars,
                state,
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval='1h',
                start_time=start_time,
                end_time=end_time,
            ),
            timeout=_DATA_LOAD_RESPONSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail='Market data load timed out') from exc
    if not bars:
        raise HTTPException(status_code=404, detail='ticker data not found')
    first = bars[0]
    last = bars[-1]
    volume = sum(float(bar.volume or 0.0) for bar in bars)
    quote_volume = sum(float(bar.volume or 0.0) * float(bar.close) for bar in bars)
    change_percent = 0.0 if first.open == 0 else ((last.close - first.open) / first.open) * 100.0
    return {
        'exchange': exchange_id,
        'market_type': query.instrument.market,
        'symbol': symbol_id,
        'lastPrice': last.close,
        'priceChangePercent': change_percent,
        'volume': volume,
        'quoteVolume': quote_volume,
    }


def _series_status(rows: list[dict[str, object]], enabled: bool) -> str:
    if not enabled:
        return 'disabled'
    if not rows:
        return 'available'
    statuses = {str(row.get('status') or 'unknown') for row in rows}
    if statuses <= {'actual'}:
        return 'actual'
    if 'stale' in statuses or 'actual' in statuses:
        return 'stale'
    return 'cached'


def _series_market_key(row: dict[str, object]) -> tuple[str, str] | None:
    exchange = str(row.get('exchange') or '').strip().lower()
    market = str(row.get('market_type') or '').strip().lower()
    if not exchange or not market:
        return None
    return exchange, market


def _config_marketdata_settings(config: object) -> dict[str, object]:
    return {
        'timeframes': list(getattr(config, 'marketdata_timeframes', ('1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d'))),
        'default_timeframe': str(getattr(config, 'marketdata_default_timeframe', '1h')),
        'stable_quotes_only': bool(getattr(config, 'marketdata_stable_quotes_only', True)),
        'stable_quote_assets': list(getattr(config, 'marketdata_stable_quote_assets', ('USDT', 'USDC'))),
    }


def _data_health_payload(state: GatewayState, *, summary: dict[str, object] | None = None) -> dict[str, object]:
    metadata = _market_metadata_payload()
    summary = summary if summary is not None else _data_summary(state)
    series = [dict(item) for item in cast(list[dict[str, object]], summary.get('series') or [])]

    by_market: dict[tuple[str, str], list[dict[str, object]]] = {}
    unknown_series = 0
    for row in series:
        key = _series_market_key(row)
        if key is None:
            unknown_series += 1
            continue
        by_market.setdefault(key, []).append(row)

    exchange_payloads: list[dict[str, object]] = []
    market_total = 0
    enabled_total = 0
    for exchange in cast(list[dict[str, object]], metadata['exchanges']):
        exchange_id = str(exchange['id'])
        enabled = bool(exchange.get('openpine_enabled'))
        if enabled:
            enabled_total += 1
        markets: list[dict[str, object]] = []
        cached_series = 0
        actual_series = 0
        stale_series = 0
        for market in cast(list[dict[str, object]], exchange.get('market_types') or []):
            market_id = str(market['id'])
            rows = by_market.get((exchange_id, market_id), [])
            market_total += 1
            market_cached = len(rows)
            market_actual = sum(1 for row in rows if str(row.get('status')) == 'actual')
            market_stale = sum(1 for row in rows if str(row.get('status')) == 'stale')
            cached_series += market_cached
            actual_series += market_actual
            stale_series += market_stale
            markets.append({
                'id': market_id,
                'label': market.get('label') or market_id,
                'enabled': enabled and bool(market.get('enabled_for_strategy_create', True)),
                'status': _series_status(rows, enabled),
                'cached_series': market_cached,
                'actual_series': market_actual,
                'stale_series': market_stale,
                'symbols': sorted({str(row.get('symbol')) for row in rows if row.get('symbol')}),
                'timeframes': sorted({str(row.get('timeframe')) for row in rows if row.get('timeframe')}),
            })
        exchange_payloads.append({
            'id': exchange_id,
            'name': exchange.get('name') or exchange_id,
            'rank': exchange.get('rank') or 999,
            'enabled': enabled,
            'status': _series_status(
                [row for (row_exchange, _row_market), rows in by_market.items() for row in rows if row_exchange == exchange_id],
                enabled,
            ),
            'cached_series': cached_series,
            'actual_series': actual_series,
            'stale_series': stale_series,
            'markets': markets,
        })

    actual_total = sum(1 for row in series if str(row.get('status')) == 'actual')
    stale_total = sum(1 for row in series if str(row.get('status')) == 'stale')
    return {
        'source': 'marketdata_provider.exchanges.registry + openpine.cache',
        'generated_at': int(time.time() * 1000),
        'settings': _config_marketdata_settings(state.config),
        'totals': {
            'exchanges': len(exchange_payloads),
            'enabled_exchanges': enabled_total,
            'market_types': market_total,
            'cached_series': len(series),
            'cached_exchanges': len({key[0] for key in by_market}),
            'cached_markets': len(by_market),
            'actual_series': actual_total,
            'stale_series': stale_total,
            'unknown_series': unknown_series,
        },
        'exchanges': exchange_payloads,
    }


@router.get('/data/health')
def data_health(state: GatewayState = Depends(get_state)) -> dict[str, object]:
    return _data_health_payload(state)


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
    if (
        int(series["latest_ms"]) + duration_ms >= now_ms
        and series.get("status") == "actual"
    ):
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
    order_ids = [
        row[0]
        for row in state.storage.execute(
            f"SELECT order_id FROM orders {where_sql}", tuple(params)
        ).fetchall()
    ]
    if not order_ids:
        return {"status": "deleted", "orders_deleted": 0, "fills_deleted": 0}

    placeholders = ",".join("?" for _ in order_ids)
    with state.storage.transaction():
        state.storage.execute(
            f"DELETE FROM fills WHERE order_id IN ({placeholders})", tuple(order_ids)
        )
        fills_deleted = state.storage.execute("SELECT changes()").fetchone()[0]
        state.storage.execute(
            f"DELETE FROM orders WHERE order_id IN ({placeholders})", tuple(order_ids)
        )
        orders_deleted = state.storage.execute("SELECT changes()").fetchone()[0]
    return {
        "status": "deleted",
        "orders_deleted": orders_deleted,
        "fills_deleted": fills_deleted,
    }


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
    background_tasks.add_task(
        _run_data_backfill_job, job.id, dict(job.input or {}), state
    )
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


async def _run_data_backfill_job(
    job_id: str, payload: dict[str, object], state: GatewayState
) -> None:
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
        pct = (
            0.99
            if phase == "write"
            else min(0.98, bars_done / total_bars) if total_bars > 0 else 0.2
        )
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
            (
                f"Writing candles: {bars_done:,} bars"
                if phase == "write"
                else (
                    f"Loading candles: {bars_done:,}/{total_bars:,} bars"
                    if total_bars
                    else "Loading candles..."
                )
            ),
            detail=detail,
        )

    try:
        result = await asyncio.to_thread(
            _run_data_backfill_sync, payload, state, _progress
        )
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
        ws_manager.update_progress(
            job_id, "data_backfill", "failed", 0.0, str(exc), detail=payload
        )
        await ws_manager.broadcast_progress(job_id)
        log.warning("backfill_failed", job_id=job_id, error=str(exc))


def _run_data_backfill_sync(
    payload: dict[str, object], state: GatewayState, progress_callback
):
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    covered, skipped_existing = _stored_ranges_cover_request(payload, state)
    if covered:
        return {
            "bars_loaded": 0,
            "skipped_existing": skipped_existing,
            "coverage_complete": True,
            "fast_skipped": True,
        }

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
    progress_callback(len(series.bars), 0, len(series.bars), 0, None, "write")
    bars_loaded, skipped_existing = _store_backfill_series(state, series)
    return {
        "bars_loaded": bars_loaded,
        "skipped_existing": skipped_existing,
        "coverage_complete": bool(getattr(series.coverage, "is_complete", False)),
    }


def _stored_ranges_cover_request(
    payload: dict[str, object], state: GatewayState
) -> tuple[bool, int]:
    exchange = str(payload["exchange"]).lower()
    market_type = str(payload["market_type"]).lower()
    symbol = str(payload["symbol"]).upper()
    timeframe = str(payload["timeframe"])
    start_ms = int(payload["from_time"])
    end_ms = int(payload["to_time"])

    groups: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    _merge_persistent_cache_groups(groups)
    _merge_marketdata_segment_groups(state, groups)
    _merge_candle_manifest_groups(state, groups)
    entry = groups.get((exchange, market_type, symbol, "trade", timeframe))
    if not entry:
        return False, 0

    ranges = _coalesce_ranges(list(entry.get("ranges") or []), timeframe)
    covered = _ranges_cover_request(ranges, timeframe, start_ms, end_ms)
    skipped_existing = (
        _estimate_bars_for_window(start_ms, end_ms, timeframe) if covered else 0
    )
    return covered, skipped_existing


def _ranges_cover_request(
    ranges: list[dict[str, object]], timeframe: str, start_ms: int, end_ms: int
) -> bool:
    if start_ms >= end_ms:
        return False
    duration_ms = _timeframe_duration_ms(timeframe)
    cursor = start_ms
    for item in sorted(
        ranges, key=lambda range_item: int(range_item.get("from_ms") or 0)
    ):
        first = item.get("from_ms")
        last = item.get("to_ms")
        if first is None or last is None:
            continue
        first_ms = int(first)
        last_ms = int(last)
        if first_ms > cursor:
            return False
        if last_ms + duration_ms >= end_ms:
            return True
        cursor = max(cursor, last_ms + duration_ms)
    return False


def _store_backfill_series(state: GatewayState, series) -> tuple[int, int]:
    from dataclasses import replace

    from marketdata_provider.contracts import BarSeries
    from openpine.data.orchestrator import DataOrchestrator

    if not series.bars:
        return 0, 0

    storage_query = replace(
        series.query, source="storage", gap_policy="allow_with_metadata"
    )
    existing = state.orchestrator.load_bars(storage_query)
    existing_times = {bar.time for bar in existing.bars}
    new_bars = tuple(bar for bar in series.bars if bar.time not in existing_times)
    skipped_existing = len(series.bars) - len(new_bars)
    if not new_bars:
        return 0, skipped_existing

    write_series = BarSeries(
        query=series.query,
        bars=new_bars,
        coverage=DataOrchestrator.coverage_for_series(
            series.query, new_bars, "provider"
        ),
    )
    result = state.orchestrator.store_bars(write_series)
    bars_loaded = int(getattr(result, "rows_written", 0) or 0)
    skipped_existing += max(0, len(new_bars) - bars_loaded)
    return bars_loaded, skipped_existing


def _parse_date_ms(value: str) -> int:
    """Parse ISO date or ms timestamp using the configured default timezone."""
    return parse_timestamp_ms(value, 0)


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
        estimated_unique = _estimate_unique_bars(
            coverage_ranges, str(entry["timeframe"])
        )
        entry["stored_rows"] = stored_rows
        entry["bar_count"] = (
            min(estimated_unique, stored_rows) if stored_rows else estimated_unique
        )
        entry["raw_range_count"] = len(ranges)
        entry["ranges"] = _compact_ranges(coverage_ranges)
        entry["role"] = _series_role(entry)
    return sorted(
        groups.values(), key=lambda item: (str(item["symbol"]), str(item["timeframe"]))
    )


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


def _merge_persistent_cache_groups(
    groups: dict[tuple[str, str, str, str, str], dict[str, object]],
) -> None:
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
            size = meta_path.stat().st_size + (
                csv_path.stat().st_size if csv_path.exists() else 0
            )
            _extend_series(
                entry, rows, first, last, size, "persistent_cache", meta_path.stem
            )
        except Exception as exc:
            log.warning(
                "data_cache_inventory_error", path=str(meta_path), error=str(exc)
            )


def _merge_marketdata_segment_groups(
    state: GatewayState, groups: dict[tuple[str, str, str, str, str], dict[str, object]]
) -> None:
    root = _marketdata_store_root(state)
    index_path = root / "index.sqlite"
    if not index_path.exists():
        return
    touched: set[tuple[str, str, str, str, str]] = set()
    try:
        with sqlite3.connect(index_path) as db:
            rows = db.execute("""
                SELECT id, exchange, market, symbol, timeframe, start_time, end_time, rows_count, source_kind
                FROM marketdata_segments
                """).fetchall()
    except Exception as exc:
        log.warning(
            "marketdata_store_inventory_error", path=str(index_path), error=str(exc)
        )
        return

    for (
        segment_id,
        exchange,
        market,
        symbol,
        timeframe,
        start_time,
        end_time,
        rows_count,
        source_kind,
    ) in rows:
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
            _dir_size(
                _marketdata_segment_dir(
                    root, exchange, market, symbol, timeframe, str(source_kind)
                )
            )
            for source_kind in source_kinds
        )
        entry["size_bytes"] = int(entry.get("size_bytes") or 0) + size


def _merge_candle_manifest_groups(
    state: GatewayState, groups: dict[tuple[str, str, str, str, str], dict[str, object]]
) -> None:
    try:
        rows = state.storage.execute("""
            SELECT exchange, market_type, symbol, price_type, timeframe,
                   min_open_time, max_open_time, row_count, file_size_bytes, manifest_id
            FROM candle_manifests
            WHERE COALESCE(is_active, 1) = 1
            """).fetchall()
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
        _extend_series(
            entry,
            int(row[7] or 0),
            row[5],
            row[6],
            int(row[8] or 0),
            "candle_store",
            row[9],
        )


def _series_entry(
    groups: dict[tuple[str, str, str, str, str], dict[str, object]],
    group_key: tuple[str, str, str, str, str],
) -> dict[str, object]:
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


def _extend_series(
    entry: dict[str, object],
    rows: int,
    first: object,
    last: object,
    size: int,
    source: str,
    source_id: str,
) -> None:
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
        entry["earliest_ms"] = (
            first_ms if current is None else min(int(current), first_ms)
        )
    if last_ms is not None:
        current = entry.get("latest_ms")
        entry["latest_ms"] = last_ms if current is None else max(int(current), last_ms)
    ranges = list(entry.get("ranges") or [])
    ranges.append(
        {
            "from_ms": first_ms,
            "to_ms": last_ms,
            "rows": rows,
            "source": source,
            "source_id": source_id,
        }
    )
    entry["ranges"] = ranges
    entry["status"] = _freshness_status(entry.get("latest_ms"), str(entry["timeframe"]))


def _compact_ranges(
    ranges: list[dict[str, object]], limit: int = 6
) -> list[dict[str, object]]:
    ordered = sorted(ranges, key=lambda item: int(item.get("from_ms") or 0))
    if len(ordered) <= limit:
        return ordered
    return ordered[:3] + [{"collapsed": len(ordered) - 5}] + ordered[-2:]


def _coalesce_ranges(
    ranges: list[dict[str, object]], timeframe: str
) -> list[dict[str, object]]:
    duration_ms = _timeframe_duration_ms(timeframe)

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
        merged[-1]["rows"] = int(merged[-1].get("rows") or 0) + int(
            item.get("rows") or 0
        )
        merged_sources = set(merged[-1].get("sources") or [])
        merged_sources.update(set(item.get("sources") or []))
        merged[-1]["sources"] = merged_sources

    for item in merged:
        sources = sorted(set(item.get("sources") or []))
        item["source"] = ",".join(sources)
        item.pop("sources", None)
    return merged


def _estimate_unique_bars(ranges: list[dict[str, object]], timeframe: str) -> int:
    duration_ms = _timeframe_duration_ms(timeframe)

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


def _estimate_bars_for_window(start_ms: int, end_ms: int, timeframe: str) -> int:
    if end_ms <= start_ms:
        return 0
    return ((end_ms - 1 - start_ms) // _timeframe_duration_ms(timeframe)) + 1


def _timeframe_duration_ms(timeframe: str) -> int:
    try:
        from marketdata_provider.contracts import parse_timeframe

        return int(parse_timeframe(timeframe).duration_ms or 60_000)
    except Exception:
        return 60_000


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
    return sum(
        (path.stat().st_size if path.exists() else 0)
        for path in (
            sqlite_path,
            sqlite_path.with_suffix(sqlite_path.suffix + "-wal"),
            sqlite_path.with_suffix(sqlite_path.suffix + "-shm"),
        )
    )


def _persistent_cache_size_bytes() -> int:
    return _dir_size(default_cache_dir())


def _candle_store_size_bytes(state: GatewayState) -> int:
    return _dir_size(_marketdata_store_root(state))


def _marketdata_store_root(state: GatewayState) -> Path:
    cache_dir = state.config.data_cache_root or (state.config.data_dir / "cache")
    return cache_dir / "marketdata"


def _marketdata_segment_dir(
    root: Path,
    exchange: str,
    market: str,
    symbol: str,
    timeframe: str,
    source_kind: str,
) -> Path:
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


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_candle_partition_path(state: GatewayState, partition_path: object) -> Path | None:
    if not partition_path:
        return None
    path = Path(str(partition_path)).expanduser()
    config = getattr(state, "config", None)
    data_dir = getattr(config, "data_dir", None)
    if data_dir is None:
        return path
    root = (Path(data_dir).expanduser() / "candles").resolve()
    resolved = path.resolve(strict=False)
    if not _path_is_under(resolved, root):
        log.warning(
            "unsafe_candle_manifest_partition_path",
            path=str(path),
            allowed_root=str(root),
        )
        return None
    return path


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def _orders_summary(state: GatewayState) -> dict[str, object]:
    total, min_ts, max_ts = state.storage.execute(
        "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM orders"
    ).fetchone()
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
        for row in state.storage.execute("""
            SELECT o.symbol, o.strategy_id, s.name, o.status, COUNT(*), MAX(o.created_at)
            FROM orders o
            LEFT JOIN strategy_instances s ON s.strategy_id = o.strategy_id
            GROUP BY o.symbol, o.strategy_id, s.name, o.status
            ORDER BY MAX(o.created_at) DESC
            """).fetchall()
    ]
    return {
        "total": total or 0,
        "earliest_ms": min_ts,
        "latest_ms": max_ts,
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
    }


def _delete_persistent_cache_series(series: dict[str, object]) -> int:
    deleted = 0
    trash_dir = (
        Path.cwd() / ".openpine" / "trash" / f"data-cache-{int(time.time() * 1000)}"
    )
    for meta_path in default_cache_dir().glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            key = meta.get("key") or {}
            instrument = key.get("instrument") or {}
            if (
                str(instrument.get("exchange", "")).lower() == str(series["exchange"])
                and str(instrument.get("market", "")).lower()
                == str(series["market_type"])
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


def _delete_marketdata_segment_series(
    state: GatewayState, series: dict[str, object]
) -> int:
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
            log.warning(
                "marketdata_store_delete_index_error",
                path=str(index_path),
                error=str(exc),
            )

    trash_dir = (
        Path.cwd()
        / ".openpine"
        / "trash"
        / f"marketdata-store-{int(time.time() * 1000)}"
    )
    root_resolved = root.resolve()
    for source_kind in source_kinds:
        path = _marketdata_segment_dir(
            root, exchange, market, symbol, timeframe, str(source_kind)
        )
        if not _path_is_under(path.resolve(strict=False), root_resolved):
            log.warning(
                "unsafe_marketdata_segment_path",
                path=str(path),
                allowed_root=str(root_resolved),
            )
            continue
        if not path.exists():
            continue
        trash_dir.mkdir(parents=True, exist_ok=True)
        target = trash_dir / path.name
        if target.exists():
            target = trash_dir / f"{path.name}-{int(time.time() * 1000)}"
        shutil.move(str(path), str(target))
        deleted += 1
    return deleted


def _delete_candle_manifest_series(
    state: GatewayState, series: dict[str, object]
) -> int:
    rows = state.storage.execute(
        """
        SELECT manifest_id, partition_path FROM candle_manifests
        WHERE exchange = ? AND market_type = ? AND symbol = ? AND price_type = ? AND timeframe = ?
        """,
        (
            series["exchange"],
            series["market_type"],
            series["symbol"],
            series["price_type"],
            series["timeframe"],
        ),
    ).fetchall()
    if not rows:
        return 0
    trash_dir = (
        Path.cwd() / ".openpine" / "trash" / f"candle-store-{int(time.time() * 1000)}"
    )
    with state.storage.transaction():
        for manifest_id, partition_path in rows:
            path = _safe_candle_partition_path(state, partition_path)
            if path is not None and path.exists():
                trash_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(trash_dir / path.name))
            state.storage.execute(
                "DELETE FROM candle_manifests WHERE manifest_id = ?", (manifest_id,)
            )
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
