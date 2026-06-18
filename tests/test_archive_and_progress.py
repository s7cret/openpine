from __future__ import annotations

from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException
from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.data.direct_provider import DirectBinanceProvider
from openpine.data.orchestrator import DataOrchestrator
from openpine.data.persistent_cache import save_bar_series
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


def _query(total_bars: int = 2_500) -> BarQuery:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return BarQuery(
        instrument=inst,
        timeframe=tf,
        start_ms=0,
        end_ms=(total_bars - 1) * 60_000,
        gap_policy="allow_with_metadata",
    )


def _bars(query: BarQuery, total_bars: int) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            instrument=query.instrument,
            timeframe=query.timeframe,
            time=i * 60_000,
            time_close=(i + 1) * 60_000,
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10.0,
            closed=True,
        )
        for i in range(total_bars)
    )


def _series(query: BarQuery, total_bars: int) -> BarSeries:
    bars = _bars(query, total_bars)
    coverage = CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        missing_intervals=(),
        duplicate_timestamps=(),
        source_mix=("persistent_cache",),
        status="valid",
    )
    return BarSeries(query=query, bars=bars, coverage=coverage)


class _FakeUrlopenResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


def test_direct_binance_provider_reports_every_fetched_page(monkeypatch):
    query = _query(2_500)
    batches = [
        [[i * 60_000, "1", "2", "0.5", "1.5", "10"] for i in range(1_000)],
        [[i * 60_000, "1", "2", "0.5", "1.5", "10"] for i in range(1_000, 2_000)],
        [[i * 60_000, "1", "2", "0.5", "1.5", "10"] for i in range(2_000, 2_500)],
    ]
    calls = iter([[[0, "1", "2", "0.5", "1.5", "10"]], *batches])

    def fake_urlopen(_request, timeout=15):
        return _FakeUrlopenResponse(next(calls))

    events = []
    monkeypatch.setattr("openpine.data.direct_provider.cache_enabled_by_env", lambda: False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    DirectBinanceProvider().fetch_bars(
        query,
        progress_callback=lambda *args: events.append(args),
    )

    fetch_events = [event for event in events if event[-1] == "fetch"]
    assert [(event[0], event[1]) for event in fetch_events] == [
        (1_000, 1),
        (2_000, 2),
        (2_500, 3),
    ]


def test_persistent_cache_reports_chunked_load_progress(tmp_path):
    query = _query(2_500)
    save_bar_series(tmp_path, _series(query, 2_500))

    events = []
    orchestrator = DataOrchestrator(
        store=SimpleNamespace(),
        cache_dir=tmp_path,
        cache_enabled=True,
    )
    loaded = orchestrator.load_bars(
        query,
        progress_callback=lambda *args: events.append(args),
    )

    cache_read_events = [event for event in events if event[-1] == "cache_read"]
    assert len(loaded.bars) == 2_500
    assert [(event[0], event[1]) for event in cache_read_events] == [
        (0, 0),
        (1_000, 1),
        (2_000, 2),
        (2_500, 3),
    ]
    assert events[-1][-1] == "cache_hit"
