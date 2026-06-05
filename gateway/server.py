"""OpenPine Web Gateway — FastAPI application factory."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openpine.gateway.config import GatewayConfig
from openpine.gateway.deps import GatewayState
from openpine.gateway.routes import accounts_data, backtest, dashboard, events, optimizer, orders_positions, pine_ops, pine_sources, strategies, trading

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared state on startup, close on shutdown."""
    state = GatewayState()
    state._startup_time = time.time()
    app.state.gateway = state
    log.info("gateway_started", sqlite=str(state.config.sqlite_path))

    # Mark stuck "running" backtests as failed (gateway was restarted)
    try:
        stuck_runs = state.storage.execute(
            "SELECT run_id FROM backtest_runs WHERE status = 'running'"
        ).fetchall()
        if stuck_runs:
            now = int(time.time() * 1000)
            for (run_id,) in stuck_runs:
                state.storage.execute(
                    "UPDATE backtest_runs SET status = 'failed', error_message = ?, finished_at = ?, updated_at = ? WHERE run_id = ?",
                    ("Gateway restarted while backtest was running", now, now, run_id),
                )
            state.storage.commit()
            log.info("marked_stuck_backtests_failed", count=len(stuck_runs))
    except Exception as exc:
        log.warning("stuck_backtest_cleanup_error", error=str(exc))

    # Start periodic bar fetcher for enabled strategies
    from openpine.data.periodic_fetcher import PeriodicBarFetcher, RefreshConfig
    fetcher_config = RefreshConfig(interval_seconds=60.0, lookback_bars=2, source_timeframe="1m")
    fetcher = PeriodicBarFetcher(
        config=fetcher_config,
        registry=state.strategy_registry,
        orchestrator=state.orchestrator,
    )
    fetcher.start()
    state._fetcher = fetcher  # expose to dashboard route
    log.info("periodic_fetcher_started", interval=fetcher_config.interval_seconds)

    # Start live strategy runner for running strategies
    from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig
    runner = LiveStrategyRunner(
        config=RunnerConfig(check_interval_seconds=5.0),
        registry=state.strategy_registry,
        orchestrator=state.orchestrator,
        storage=state.storage,
        artifact_store=state.artifact_store,
    )
    runner.start()
    state._live_runner = runner
    log.info("live_runner_started")

    yield

    runner.stop()
    fetcher.stop()
    state.close()
    log.info("gateway_stopped")


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or GatewayConfig()

    app = FastAPI(
        title="OpenPine Gateway",
        description="Web API for the OpenPine Pine stack — strategies, backtests, live trading, and market data.",
        version="2.17.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount route modules under /api
    api_prefix = cfg.api_prefix
    app.include_router(dashboard.router, prefix=api_prefix)
    app.include_router(pine_sources.router, prefix=api_prefix)
    app.include_router(pine_ops.router, prefix=api_prefix)
    app.include_router(strategies.router, prefix=api_prefix)
    app.include_router(backtest.router, prefix=api_prefix)
    app.include_router(trading.router, prefix=api_prefix)
    app.include_router(orders_positions.router, prefix=api_prefix)
    app.include_router(events.router, prefix=api_prefix)
    app.include_router(accounts_data.router, prefix=api_prefix)
    app.include_router(optimizer.router, prefix=api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": "2.17.0"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": "OpenPine Gateway",
            "version": "2.17.0",
            "docs": "/docs",
            "api": api_prefix,
        }

    return app
