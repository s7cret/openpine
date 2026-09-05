from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.jobs import Job, JobScheduler, JobStatus, JobType
from openpine.registry.strategies import StrategyInstance
from openpine.state.store import StateStore
from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.strategy_ledger import LedgerSource, StrategyLedger
from openpine.workers.strategy_job_executor import (
    StrategyJobExecutor,
    StrategyJobStatus,
    _paper_broker_identity,
)


def _strategy() -> StrategyInstance:
    strategy = StrategyInstance(
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
        status="running",
        created_at=0,
        updated_at=0,
    )
    strategy.semantic_profile = "strict_5x"
    return strategy


def test_paper_broker_identity_is_bound_to_exact_engine_wheel_and_strategy_state() -> None:
    adapter_ref, account_ref = _paper_broker_identity(
        _strategy(),
        SimpleNamespace(
            wheel_identities=(
                ("openpine", "5.0.0rc5", "sha256:" + "a" * 64),
                ("backtest-engine", "5.0.0rc5", "sha256:" + "b" * 64),
            )
        ),
        paper_epoch_start=0,
    )

    assert adapter_ref == ("urn:openpine:paper-broker:backtest-engine:5.0.0rc5:sha256:" + "b" * 64)
    assert account_ref == "urn:openpine:paper-account:strategy-1:artifact-1:params-1:0"


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
            "semantic_profile": "strict_5x",
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

    def run_isolated(self, source, bars, config, **kwargs):
        self.calls.append((source, bars, config, kwargs))
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


def test_strategy_job_executor_rejects_persisted_job_for_paused_strategy(
    tmp_path,
) -> None:
    strategy = _strategy()
    strategy.enabled = False
    strategy.status = "paused"
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job())
    executor = StrategyJobExecutor(
        registry=_Registry(strategy),
        orchestrator=_Orchestrator(_bar()),
        scheduler=scheduler,
        state_store=StateStore(tmp_path / "state"),
        runtime_adapter=_RuntimeAdapter(result=_runtime_result(_bar())),
    )

    result = executor.process(job)

    assert result.status == StrategyJobStatus.FAILED
    assert "not active" in (result.error or "")


def test_target_bar_load_normalizes_inclusive_close_to_exclusive_query_end() -> None:
    timeframe = parse_timeframe("15m")
    bar = Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=timeframe,
        time=0,
        time_close=(timeframe.duration_ms or 0) - 1,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=42.0,
        closed=True,
    )
    captured = []

    class InclusiveStore:
        def get_bars(self, query):
            captured.append(query)
            return [bar] if query.end_ms == timeframe.duration_ms else []

    executor = StrategyJobExecutor(
        registry=_Registry(_strategy()),
        orchestrator=InclusiveStore(),
        scheduler=JobScheduler(),
        state_store=SimpleNamespace(),
    )

    loaded = executor._load_target_bar(
        _strategy(),
        {
            "instrument_key": "binance:spot:BTCUSDT:trade",
            "timeframe": "15m",
            "bar_time": bar.time,
            "bar_close_time": bar.time_close,
        },
    )

    assert loaded is bar
    assert captured[0].end_ms == timeframe.duration_ms


def test_paper_replay_loads_all_closed_bars_from_activation_boundary() -> None:
    timeframe = parse_timeframe("15m")
    duration_ms = timeframe.duration_ms or 0
    strategy = _strategy()
    strategy.updated_at = duration_ms + 1
    bars = [_bar(2 * duration_ms), _bar(3 * duration_ms)]
    captured = []

    class ReplayStore:
        def get_bars(self, query):
            captured.append(query)
            return [bar for bar in bars if bar.time == query.start_ms]

    executor = StrategyJobExecutor(
        registry=_Registry(strategy),
        orchestrator=ReplayStore(),
        scheduler=JobScheduler(),
        state_store=SimpleNamespace(),
    )

    replay = executor._load_paper_replay_bars(strategy, bars[-1])

    assert replay == bars
    assert [query.start_ms for query in captured] == [2 * duration_ms, 3 * duration_ms]
    assert captured[-1].end_ms == 4 * duration_ms


def _storage(tmp_path):
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def test_strategy_job_executor_processes_bar_and_saves_snapshot_and_ledger(
    tmp_path, monkeypatch,
) -> None:
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
        )
        executor._stamped_sources[("pine-1", "artifact-1")] = b"generated"
        monkeypatch.setattr(executor, "_bind_isolated_config", lambda *args: None)

        result = executor.process(job)

        assert result.status == StrategyJobStatus.DONE
        assert result.snapshot_id
        assert result.trades_recorded == 1
        assert scheduler.get_job(job.id).status == JobStatus.DONE
        assert adapter.calls[0][0] == b"generated"
        assert adapter.calls[0][3]["resume_state"] is None
        assert adapter.calls[0][3]["params"] == {"length": 20}
        position = ledger.get_position(
            strategy_id="strategy-1",
            account_id="urn:openpine:paper-account:strategy-1:artifact-1:params-1:0",
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="15m",
        )
        assert position is not None
        assert position.qty == 0.2
        assert [trade.source for trade in ledger.list_trades(strategy_id="strategy-1")] == [
            LedgerSource.PAPER
        ]
    finally:
        storage.close()


def test_paper_job_rebuilds_deterministically_without_restoring_partial_worker_state(
    tmp_path, monkeypatch,
) -> None:
    duration_ms = parse_timeframe("15m").duration_ms or 0
    bars = [_bar(0), _bar(duration_ms)]
    target = bars[-1]
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job(target))
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
        runtime_state={"unsafe_partial_worker_state": True},
        bar_time=0,
    )

    class ReplayStore:
        def get_bars(self, query):
            return [bar for bar in bars if bar.time == query.start_ms]

    adapter = _RuntimeAdapter(result=_runtime_result(target))
    executor = StrategyJobExecutor(
        registry=_Registry(_strategy()),
        orchestrator=ReplayStore(),
        scheduler=scheduler,
        state_store=state_store,
        runtime_adapter=adapter,
    )

    executor._stamped_sources[("pine-1", "artifact-1")] = b"generated"
    monkeypatch.setattr(executor, "_bind_isolated_config", lambda *args: None)

    result = executor.process(job)
    persisted = state_store.load_runtime_snapshot(
        "strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={
            "exchange": "binance",
            "market": "spot",
            "symbol": "BTCUSDT",
            "price_type": "trade",
        },
        timeframe={"canonical": "15m"},
    )

    assert result.status == StrategyJobStatus.DONE
    assert adapter.calls[0][1] == bars
    assert adapter.calls[0][3]["resume_state"] is None
    assert persisted["schema_version"] == "openpine.paper.evaluation.v1"
    assert persisted["resume_policy"] == "deterministic_replay"
    assert persisted["paper_epoch_policy"] == "reset_on_activation"
    assert persisted["replay_start_bar_time"] == 0
    assert persisted["processed_bar_time"] == target.time


def test_paper_job_retries_idempotently_after_ledger_publish_before_snapshot(
    tmp_path, monkeypatch,
) -> None:
    bar = _bar()
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job(bar))
    storage = _storage(tmp_path)
    inner_state = StateStore(tmp_path / "state")

    class FailFirstSnapshot:
        def __init__(self):
            self.failed = False

        def latest_snapshot_metadata(self, *args, **kwargs):
            return inner_state.latest_snapshot_metadata(*args, **kwargs)

        def save_runtime_snapshot(self, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("synthetic snapshot publication failure")
            return inner_state.save_runtime_snapshot(**kwargs)

    try:
        ledger = StrategyLedger(storage)
        executor = StrategyJobExecutor(
            registry=_Registry(_strategy()),
            orchestrator=_Orchestrator(bar),
            scheduler=scheduler,
            state_store=FailFirstSnapshot(),
            ledger=ledger,
            runtime_adapter=_RuntimeAdapter(result=_runtime_result(bar)),
        )

        executor._stamped_sources[("pine-1", "artifact-1")] = b"generated"
        monkeypatch.setattr(executor, "_bind_isolated_config", lambda *args: None)

        first = executor.process(job)
        second = executor.process(job)

        assert first.status == StrategyJobStatus.FAILED
        assert second.status == StrategyJobStatus.DONE
        assert second.snapshot_id
        assert len(ledger.list_trades(strategy_id="strategy-1")) == 1
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
    )

    result = executor.process(job)

    assert result.status == StrategyJobStatus.SKIPPED
    assert result.skipped_reason == "already_processed"
    assert scheduler.get_job(job.id).status == JobStatus.DONE
    assert adapter.calls == []


def test_strategy_job_executor_marks_failed_without_snapshot(tmp_path, monkeypatch) -> None:
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
    )
    executor._stamped_sources[("pine-1", "artifact-1")] = b"generated"
    monkeypatch.setattr(executor, "_bind_isolated_config", lambda *args: None)

    result = executor.process(job)

    assert result.status == StrategyJobStatus.FAILED
    assert "boom" in (result.error or "")
    assert scheduler.get_job(job.id).status == JobStatus.FAILED
    assert state_store.list_snapshots("strategy-1") == []


def test_strategy_job_executor_observe_mode_saves_snapshot_without_ledger(
    tmp_path, monkeypatch,
) -> None:
    bar = _bar()
    scheduler = JobScheduler()
    job = scheduler.enqueue(_job(bar))
    job.job_type = JobType.OBSERVE_BAR_PROCESS
    storage = _storage(tmp_path)
    try:
        adapter = _RuntimeAdapter(result=_runtime_result(bar))
        ledger = StrategyLedger(storage)
        strategy = _strategy()
        strategy.mode = "observe"
        executor = StrategyJobExecutor(
            registry=_Registry(strategy),
            orchestrator=_Orchestrator(bar),
            scheduler=scheduler,
            state_store=StateStore(tmp_path / "state"),
            ledger=ledger,
            runtime_adapter=adapter,
        )
        executor._stamped_sources[("pine-1", "artifact-1")] = b"generated"
        monkeypatch.setattr(executor, "_bind_isolated_config", lambda *args: None)

        result = executor.process(job)

        assert result.status == StrategyJobStatus.DONE
        assert result.snapshot_id
        assert result.trades_recorded == 0
        assert ledger.list_trades(strategy_id="strategy-1") == []
        assert (
            ledger.get_position(
                strategy_id="strategy-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
            )
            is None
        )
    finally:
        storage.close()


def test_delegated_job_loads_all_mtf_series_from_payload(monkeypatch, tmp_path) -> None:
    bar = _bar()
    job = _job(bar)
    assert job.input is not None
    job.input["mtf_series"] = [
        {"symbol": "BTCUSDT", "timeframe": "1D"},
        {"symbol": "ETHUSDT", "timeframe": "4h"},
    ]
    scheduler = JobScheduler()
    job = scheduler.enqueue(job)
    loaded: list[tuple[str, str]] = []
    seen: dict[str, object] = {}

    class Orchestrator(_Orchestrator):
        def load_bars(self, query: BarQuery):
            key = (query.instrument.symbol, query.timeframe.canonical)
            loaded.append(key)
            duration = query.timeframe.duration_ms or 60_000
            mtf_bar = Bar(
                instrument=query.instrument,
                timeframe=query.timeframe,
                time=0,
                time_close=duration - 1,
                open=2,
                high=3,
                low=1,
                close=2,
                volume=1,
                closed=True,
            )
            return SimpleNamespace(bars=(mtf_bar,))

    class Adapter:
        def run_isolated(
            self,
            source,
            bars,
            config,
            resume_state=None,
            htf_bars=None,
            params=None,
        ):
            seen["source"] = source
            seen["htf_bars"] = htf_bars
            seen["params"] = params
            return _runtime_result(bar)

    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *args, **kwargs: b"STAMPED",
    )
    executor = StrategyJobExecutor(
        registry=_Registry(_strategy()),
        orchestrator=Orchestrator(bar),
        scheduler=scheduler,
        state_store=StateStore(tmp_path / "state"),
        runtime_adapter=Adapter(),
    )
    monkeypatch.setattr(executor, "_bind_isolated_config", lambda *args: None)

    result = executor.process(job)

    assert result.status == StrategyJobStatus.DONE
    assert loaded == [("BTCUSDT", "1D"), ("ETHUSDT", "4h")]
    assert seen["source"] == b"STAMPED"
    assert seen["params"] == {"length": 20}
    assert {(item["symbol"], item["timeframe"]) for item in seen["htf_bars"]} == {
        ("BTCUSDT", "1D"),
        ("ETHUSDT", "4h"),
    }
