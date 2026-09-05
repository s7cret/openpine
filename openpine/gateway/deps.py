"""Dependency injection for gateway routes.

All database/service instances are created once at startup and
provided to route handlers via FastAPI's Depends() mechanism.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any

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
        deployment_manifest = getattr(self.config, "deployment_manifest", None)
        deployment_wheelhouse = getattr(self.config, "deployment_wheelhouse", None)
        if (deployment_manifest is None) != (deployment_wheelhouse is None):
            raise RuntimeError(
                "both deployment_manifest and deployment_wheelhouse are required"
            )
        self.admission_identity = None
        self.admitted_manifest = None
        if deployment_manifest is not None and deployment_wheelhouse is not None:
            from openpine.admission import load_active_deployment_identity
            from openpine.runtime.admitted_manifest import load_admitted_manifest

            self.admitted_manifest = load_admitted_manifest(deployment_manifest)
            self.admission_identity = load_active_deployment_identity(
                deployment_manifest,
                deployment_wheelhouse,
            )
        self.storage = SQLiteStorage(self.config.sqlite_path)
        # Schema compatibility is a startup invariant: managers below issue
        # queries that require the latest migrations. Never run against a
        # partially migrated database.
        from openpine.storage.migrations import MigrationRunner

        try:
            MigrationRunner().run_migrations(self.storage)
        except BaseException:
            self.storage.close()
            raise
        self.pine_registry = SQLitePineSourceRegistry(self.config.sqlite_path)
        self.strategy_registry = SQLiteStrategyRegistry(self.config.sqlite_path)
        self.backtest_store = BacktestResultStore(self.storage)
        recover_incomplete_runs = getattr(
            self.backtest_store, "recover_incomplete_runs", None
        )
        recovered_backtests = (
            recover_incomplete_runs() if callable(recover_incomplete_runs) else 0
        )
        if recovered_backtests:
            import structlog

            structlog.get_logger(__name__).warning(
                "incomplete_backtests_recovered", count=recovered_backtests
            )
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
        from openpine.jobs.persist import JobV1Store

        self.job_store = JobV1Store(self.config.data_dir / "jobs-v1.sqlite")
        self.artifact_store = ArtifactStore()
        self.state_store = StateStore(self.config.data_dir / "state")
        # Set up data orchestrator with canonical marketdata-provider runtime.
        from openpine.data import orchestrator as orchestrator_mod
        from openpine.data.provider_adapter import create_local_marketdata_provider_adapter

        # Gateway background runners must read current storage/provider state.
        # Persistent CLI cache can turn live polling into stale/cache-heavy work.
        self.orchestrator = orchestrator_mod.DataOrchestrator(cache_enabled=False)
        try:
            data_cache_root = getattr(self.config, "data_cache_root", None) or (self.config.data_dir / "cache")
            self.orchestrator.set_provider(
                create_local_marketdata_provider_adapter(
                    cache_dir=data_cache_root / "marketdata"
                )
            )
        except Exception as exc:
            import structlog

            structlog.get_logger(__name__).warning(
                "marketdata_provider_init_error", error=str(exc)
            )
        self.risk_manager = RiskManager(self.config.kill_switch)
        self._risk_kill_switch = self.risk_manager._kill_switch
        self.backtest_cancel_requests: set[str] = set()
        self._lock = threading.Lock()
        self.strategy_activation_lock = threading.RLock()
        self._startup_time = 0.0
        self._background_worker_process: Any | None = None
        self._background_worker_supervisor: Any | None = None
        self._fetcher: Any | None = None
        self._live_runner: Any | None = None

        # Achievement engine: depends on storage. Seed the catalog on every
        # start (idempotent INSERT OR REPLACE) and run an initial recompute
        # so the UI shows real numbers on first paint.
        from openpine.achievements.engine import AchievementEngine
        from openpine.achievements.seed import seed_achievement_i18n, seed_achievements

        try:
            seed_achievements(self.storage)
            seed_achievement_i18n(self.storage)
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).warning(
                "achievement_seed_error_non_fatal", error=str(exc)
            )
        self.achievement_engine = AchievementEngine(self.storage)
        try:
            # recompute_stats() rebuilds achievement_stats; refresh()
            # also runs check_unlocks() so newly-met targets land in
            # achievement_unlocks on startup. Without this the UI sees
            # 0 unlocked until the 5-min background tick fires.
            self.achievement_engine.refresh()
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).warning(
                "achievement_recompute_error_non_fatal", error=str(exc)
            )

    def close(self) -> None:
        """Release resources."""
        self.storage.close()
        self.pine_registry.close()
        self.strategy_registry.close()
        closer = getattr(self.job_store, "close", None)
        if callable(closer):
            closer()


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
