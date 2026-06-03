from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.jobs import Job, JobScheduler, JobStatus, JobType
from openpine.registry.strategies import StrategyInstance
from openpine.state.store import StateStore
from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.strategy_ledger import LedgerSource, StrategyLedger
from openpine.workers.strategy_job_executor import StrategyJobExecutor, StrategyJobStatus


class _DummyStrategy:
    pass


def _strategy() -> StrategyInstance:
    return StrategyInstance(
        strategy_id="strategy-1",
        name="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_json='{"length": 20}',
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode="paper",
        enabled=True,
    )


def _bar(open_time: int = 0) -> Bar:
    tf = parse_timeframe("15m")
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=tf,
        time=open_time,
        time_close=open_time + (tf.duration_ms or 0),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=42.0,
        closed=True,
    )


def _job(bar: Bar | None = None) -> Job:
    bar = bar or _bar()
    return Job(
        job_type=JobType.PAPER_BAR_PROCESS,
        strategy_id="strategy-1",
        idempotency_key=f"paper_bar_process:strategy-1:binance:spot:BTCUSDT:trade:15m:{bar.time}",
        serialization_key="strategy-1",
        input={
            "strategy_id": "strategy-1",
            "artifact_id": "artifact-1",
            "params_hash": "params-1",
            "instrument_key": "binance:spot:BTCUSDT:trade",
            "timeframe": "15m",
            "bar_time": bar.time,
            "bar_close_time": bar.time_close,
            "source": "live",
        },
    )


class _Registry:
    def __init__(self, strategy: StrategyInstance) -> None:
        self.strategy = strategy

    def get_strategy(self, strategy_id: str) -> StrategyInstance:
        assert strategy_id == self.strategy.strategy_id
        return self.strategy


class _Orchestrator:
    def __init__(self, bar: Bar) -> None:
        self.bar = bar

    def get_bars(self, query: BarQuery):
        if query.start_ms == self.bar.time and query.end_ms == self.bar.time_close:
            return [self.bar]
        return []


class _RuntimeAdapter:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def run(self, strategy_class, bars, config, **kwargs):
        self.calls.append((strategy_class, bars, config, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def _runtime_result(bar: Bar):
    position = SimpleNamespace(
        size=0.2,
        direction="long",
        avg_price=100.0,
        realized_profit=3.0,
        open_profit=1.0,
    )
    resume_state = SimpleNamespace(
        broker_state=SimpleNamespace(position=position),
        runtime_state={"bar": bar.time},
    )
    trade = SimpleNamespace(
        id="closed-1",
        entry_id="L",
        exit_id="XL",
        direction="long",
        entry_time=bar.time - (bar.timeframe.duration_ms or 0),
        exit_time=bar.time_close,
        entry_price=100.0,
        exit_price=105.0,
        qty=0.2,
        profit=1.0,
        commission_entry=0.01,
        commission_exit=0.01,
        bars_held=1,
    )
    raw_result = SimpleNamespace(
        status="completed",
        resume_state=resume_state,
        closed_trades=[trade],
        open_trades=[],
        net_profit=3.0,
    )
    return SimpleNamespace(status="completed", resume_state=resume_state, raw_result=raw_result)


def _storage(tmp_path):
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def test_strategy_job_executor_processes_bar_and_saves_snapshot_and_ledger(tmp_path) -> None:
    bar = _bar()
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job(bar))
    storage = _storage(tmp_path)
    try:
        adapter = _RuntimeAdapter(result=_runtime_result(bar))
        ledger = StrategyLedger(storage)
        executor = StrategyJobExecutor(
            registry=_Registry(_strategy()),
            orchestrator=_Orchestrator(bar),
            scheduler=scheduler,
            state_store=StateStore(tmp_path / "state"),
            ledger=ledger,
            runtime_adapter=adapter,
            strategy_loader=lambda strategy: _DummyStrategy,
            runtime_data_provider="runtime-provider",
        )

        result = executor.process(job)

        assert result.status == StrategyJobStatus.DONE
        assert result.snapshot_id
        assert result.trades_recorded == 1
        assert scheduler.get_job(job.id).status == JobStatus.DONE
        assert adapter.calls[0][3]["params"] == {"length": 20}
        assert adapter.calls[0][3]["resume_state"] is None
        position = ledger.get_position(
            strategy_id="strategy-1",
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="15m",
        )
        assert position is not None
        assert position.qty == 0.2
        assert [trade.source for trade in ledger.list_trades(strategy_id="strategy-1")] == [LedgerSource.PAPER]
    finally:
        storage.close()


def test_strategy_job_executor_skips_already_processed_bar(tmp_path) -> None:
    bar = _bar()
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job(bar))
    state_store = StateStore(tmp_path / "state")
    state_store.save_runtime_snapshot(
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={
            "exchange": "binance",
            "market": "spot",
            "symbol": "BTCUSDT",
            "price_type": "trade",
        },
        timeframe={"canonical": "15m"},
        runtime_state={"already": True},
        bar_time=bar.time,
    )
    adapter = _RuntimeAdapter(result=_runtime_result(bar))
    executor = StrategyJobExecutor(
        registry=_Registry(_strategy()),
        orchestrator=_Orchestrator(bar),
        scheduler=scheduler,
        state_store=state_store,
        runtime_adapter=adapter,
        strategy_loader=lambda strategy: _DummyStrategy,
        runtime_data_provider="runtime-provider",
    )

    result = executor.process(job)

    assert result.status == StrategyJobStatus.SKIPPED
    assert result.skipped_reason == "already_processed"
    assert scheduler.get_job(job.id).status == JobStatus.DONE
    assert adapter.calls == []


def test_strategy_job_executor_marks_failed_without_snapshot(tmp_path) -> None:
    bar = _bar()
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job(bar))
    state_store = StateStore(tmp_path / "state")
    executor = StrategyJobExecutor(
        registry=_Registry(_strategy()),
        orchestrator=_Orchestrator(bar),
        scheduler=scheduler,
        state_store=state_store,
        runtime_adapter=_RuntimeAdapter(error=RuntimeError("boom")),
        strategy_loader=lambda strategy: _DummyStrategy,
        runtime_data_provider="runtime-provider",
    )

    result = executor.process(job)

    assert result.status == StrategyJobStatus.FAILED
    assert "boom" in (result.error or "")
    assert scheduler.get_job(job.id).status == JobStatus.FAILED
    assert state_store.list_snapshots("strategy-1") == []
