"""Required SQLite index inventory for OpenPine v4 backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredIndex:
    """Index expected in a production-ready SQLite schema."""

    name: str
    table: str
    purpose: str


REQUIRED_INDEXES: tuple[RequiredIndex, ...] = (
    RequiredIndex("idx_pine_sources_hash", "pine_sources", "source lookup by content hash"),
    RequiredIndex("idx_pine_artifacts_pine_created", "pine_artifacts", "latest artifacts per source"),
    RequiredIndex("idx_compile_artifacts_source_created", "compile_artifacts", "latest compile artifacts per source"),
    RequiredIndex("idx_strategy_instances_pine_id", "strategy_instances", "strategy lookup by Pine source"),
    RequiredIndex("idx_strategy_instances_enabled_status", "strategy_instances", "enabled runtime strategy scan"),
    RequiredIndex("idx_jobs_ready_queue", "jobs", "ready job polling"),
    RequiredIndex("idx_jobs_lease", "jobs", "leased job recovery"),
    RequiredIndex("idx_orders_symbol_status_created", "orders", "order lookup by instrument/status"),
    RequiredIndex("idx_backtest_runs_strategy_status_time", "backtest_runs", "backtest history by strategy/status"),
    RequiredIndex("idx_candle_manifests_active_range", "candle_manifests", "active candle manifest range lookup"),
    RequiredIndex("idx_events_type_time", "events", "event replay by type/time"),
    RequiredIndex("idx_event_consumers_event_id", "event_consumers", "consumer state lookup by event"),
    RequiredIndex("idx_parquet_manifests_dataset_time", "parquet_manifests", "data lake manifest lookup by dataset/time"),
)


def required_index_names() -> tuple[str, ...]:
    """Return required index names in stable order."""

    return tuple(index.name for index in REQUIRED_INDEXES)
