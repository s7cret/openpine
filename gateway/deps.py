"""Dependency injection for gateway routes.

All database/service instances are created once at startup and
provided to route handlers via FastAPI's Depends() mechanism.
"""

from __future__ import annotations

import threading
from typing import Annotated

from fastapi import Depends, Request

from openpine.accounts.manager import AccountManager
from openpine.artifacts.store import ArtifactStore
from openpine.config import OpenPineConfig
from openpine.data.orchestrator import DataOrchestrator
from openpine.events.bus import EventBus
from openpine.jobs import JobScheduler
from openpine.orders.manager import OrderManager
from openpine.pine.registry import SQLitePineSourceRegistry
from openpine.registry.strategies import SQLiteStrategyRegistry
from openpine.risk.manager import RiskManager
from openpine.state.store import StateStore
from openpine.storage.backtest_storage import BacktestResultStore
from openpine.storage.sqlite_storage import SQLiteStorage


class GatewayState:
    """Shared state initialized once at app startup.

    Stored on app.state.gateway by the lifespan handler.
    """

    def __init__(self) -> None:
        self.config = OpenPineConfig.load()
        self.storage = SQLiteStorage(self.config.sqlite_path)
        # Run migrations before initializing managers that depend on schema.
        # Wrap in try/except for idempotency: if DB was partially migrated by
        # a prior run, duplicate-column errors are non-fatal.
        from openpine.storage.migrations import MigrationRunner
        try:
            MigrationRunner().run_migrations(self.storage)
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).warning(
                "migration_error_non_fatal", error=str(exc)
            )
        self.pine_registry = SQLitePineSourceRegistry(self.config.sqlite_path)
        self.strategy_registry = SQLiteStrategyRegistry(self.config.sqlite_path)
        self.backtest_store = BacktestResultStore(self.storage)
        self.account_manager = AccountManager(self.storage)
        self.order_manager = OrderManager(self.storage)
        # EventBus may fail if events table schema is old (pre-gateway).
        # Non-fatal — gateway can still serve other endpoints.
        try:
            self.event_bus = EventBus(self.storage)
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).warning(
                "event_bus_init_error_non_fatal", error=str(exc)
            )
            self.event_bus = None  # type: ignore[assignment]
        self.scheduler = JobScheduler()
        self.artifact_store = ArtifactStore()
        self.state_store = StateStore(self.config.data_dir / "state")
        # Set up data orchestrator with direct HTTP provider (no timeout hangs)
        from openpine.data.orchestrator import DataOrchestrator
        from openpine.data.direct_provider import DirectBinanceProvider
        # Gateway background runners must read current storage/provider state.
        # Persistent CLI cache can turn live polling into stale/cache-heavy work.
        self.orchestrator = DataOrchestrator(cache_enabled=False)
        try:
            self.orchestrator.set_provider(DirectBinanceProvider())
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).warning(
                "marketdata_provider_init_error", error=str(exc)
            )
        self._risk_kill_switch = [self.config.kill_switch]
        self.risk_manager = RiskManager(self._risk_kill_switch)
        self.backtest_cancel_requests: set[str] = set()
        self._lock = threading.Lock()

    def close(self) -> None:
        """Release resources."""
        self.storage.close()
        self.pine_registry.close()
        self.strategy_registry.close()


def get_state(request: Request) -> GatewayState:
    """Extract GatewayState from app.state."""
    return request.app.state.gateway  # type: ignore[no-any-return]


def get_pine_registry(
    state: Annotated[GatewayState, Depends(get_state)],
) -> SQLitePineSourceRegistry:
    return state.pine_registry


def get_strategy_registry(
    state: Annotated[GatewayState, Depends(get_state)],
) -> SQLiteStrategyRegistry:
    return state.strategy_registry


def get_backtest_store(
    state: Annotated[GatewayState, Depends(get_state)],
) -> BacktestResultStore:
    return state.backtest_store


def get_account_manager(
    state: Annotated[GatewayState, Depends(get_state)],
) -> AccountManager:
    return state.account_manager


def get_order_manager(
    state: Annotated[GatewayState, Depends(get_state)],
) -> OrderManager:
    return state.order_manager


def get_event_bus(
    state: Annotated[GatewayState, Depends(get_state)],
) -> EventBus:
    return state.event_bus


def get_scheduler(
    state: Annotated[GatewayState, Depends(get_state)],
) -> JobScheduler:
    return state.scheduler


def get_artifact_store(
    state: Annotated[GatewayState, Depends(get_state)],
) -> ArtifactStore:
    return state.artifact_store


def get_state_store(
    state: Annotated[GatewayState, Depends(get_state)],
) -> StateStore:
    return state.state_store


def get_orchestrator(
    state: Annotated[GatewayState, Depends(get_state)],
) -> DataOrchestrator:
    return state.orchestrator


def get_risk_manager(
    state: Annotated[GatewayState, Depends(get_state)],
) -> RiskManager:
    return state.risk_manager
