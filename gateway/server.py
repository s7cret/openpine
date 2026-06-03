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
from openpine.gateway.routes import accounts_data, backtest, dashboard, events, pine_sources, strategies, trading

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared state on startup, close on shutdown."""
    state = GatewayState()
    state._startup_time = time.time()
    app.state.gateway = state
    log.info("gateway_started", sqlite=str(state.config.sqlite_path))
    yield
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
    app.include_router(strategies.router, prefix=api_prefix)
    app.include_router(backtest.router, prefix=api_prefix)
    app.include_router(trading.router, prefix=api_prefix)
    app.include_router(events.router, prefix=api_prefix)
    app.include_router(accounts_data.router, prefix=api_prefix)

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
