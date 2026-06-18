from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import backtest, pine_sources, strategies
from openpine.gateway.schemas import StrategyCreate
from openpine.pine.registry import SQLitePineSourceRegistry
from openpine.registry.strategies import SQLiteStrategyRegistry


@pytest.mark.asyncio
async def test_strategy_archive_disables_and_blocks_start_enable(tmp_path):
    registry = SQLiteStrategyRegistry(db_path=tmp_path / "openpine.sqlite")
    strategy = registry.create_strategy(
        name="Archive me",
        pine_id="pine_1",
        artifact_id="artifact_1",
        symbol="SOLUSDT",
        timeframe="1D",
    )
    registry.set_enabled(strategy.strategy_id, True)
    registry.update_status(strategy.strategy_id, "running")

    archived = await strategies.archive_strategy(strategy.strategy_id, registry=registry)

    assert archived.archived is True
    assert archived.enabled is False
    assert archived.status == "paused"

    state = SimpleNamespace(strategy_registry=registry)
    with pytest.raises(HTTPException) as excinfo:
        await strategies.strategy_action(strategy.strategy_id, state=state, action="start")
    assert excinfo.value.status_code == 400

    restored = await strategies.unarchive_strategy(strategy.strategy_id, registry=registry)
    assert restored.archived is False
    assert restored.enabled is False

    await strategies.strategy_action(strategy.strategy_id, state=state, action="enable")
    assert registry.get_strategy(strategy.strategy_id).enabled is True


@pytest.mark.asyncio
async def test_pine_source_archive_roundtrip_and_blocks_strategy_create(tmp_path):
    pine_registry = SQLitePineSourceRegistry(db_path=tmp_path / "openpine.sqlite")
    source = pine_registry.add_source("//@version=5\nstrategy('x')", "x.pine")

    archived = await pine_sources.archive_source(source.id, registry=pine_registry)
    assert archived.archived is True
    assert pine_registry.get_source(source.id).archived is True

    state = SimpleNamespace(
        pine_registry=pine_registry,
        strategy_registry=SimpleNamespace(),
        artifact_store=SimpleNamespace(),
    )
    with pytest.raises(HTTPException) as excinfo:
        await strategies.create_strategy(
            StrategyCreate(
                name="From archived",
                pine_id=source.id,
                artifact_id="artifact_1",
                symbol="SOLUSDT",
                timeframe="1D",
            ),
            state=state,
        )
    assert excinfo.value.status_code == 400

    restored = await pine_sources.unarchive_source(source.id, registry=pine_registry)
    assert restored.archived is False
    assert pine_registry.get_source(source.id).archived is False


def test_backtest_market_data_progress_uses_bar_ratio_not_flat_twenty():
    assert backtest._backtest_market_data_pct(
        bars_fetched=0,
        pages_done=0,
        expected_bars=1_000,
        expected_pages=1,
    ) == pytest.approx(0.20)
    assert backtest._backtest_market_data_pct(
        bars_fetched=500,
        pages_done=0,
        expected_bars=1_000,
        expected_pages=1,
    ) == pytest.approx(0.275)
    assert backtest._backtest_market_data_pct(
        bars_fetched=1_000,
        pages_done=0,
        expected_bars=1_000,
        expected_pages=1,
    ) == pytest.approx(0.35)


def test_backtest_compute_progress_starts_after_data_load_and_keeps_save_gap():
    assert backtest._backtest_compute_pct(0, 100) == pytest.approx(0.35)
    assert backtest._backtest_compute_pct(50, 100) == pytest.approx(0.65)
    assert backtest._backtest_compute_pct(100, 100) == pytest.approx(0.95)
