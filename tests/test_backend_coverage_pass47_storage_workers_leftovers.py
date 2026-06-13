from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import msgpack
import pytest
from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.config import OpenPineConfig
from openpine.jobs import Job, JobScheduler, JobType
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance
from openpine.state.errors import InvalidSnapshotError
from openpine.state.store import SavePolicy, SnapshotMetadata, StateStore, StrategyState
from openpine.storage import (
    BacktestResultStore,
    BacktestRunRequest,
    BackendHealth,
    DuckDBAnalyticsAdapter,
    LedgerSource,
    MigrationRunner,
    ParquetDataLakeAdapter,
    PostgresControlStorageAdapter,
    SQLiteStorage,
    StrategyLedger,
    StrategyTrade,
    TradeStatus,
)
from openpine.storage import backup as backup_mod
from openpine.storage import migrations as migrations_mod
from openpine.storage import schema_compat
from openpine.storage import strategy_ledger as ledger_mod
from openpine.workers import strategy_fanout as fanout_mod
from openpine.workers import strategy_job_executor as job_exec_mod
from openpine.workers.strategy_fanout import (
    FanoutStatus,
    StrategyBarFanout,
    StrategyBarFanoutConfig,
)
from openpine.workers.strategy_job_executor import StrategyJobExecutor


class _DummyStrategy:
    pass


def _config(tmp_path: Path) -> OpenPineConfig:
    return OpenPineConfig(
        workspace_root=tmp_path,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        sqlite_path=tmp_path / "openpine.sqlite",
        duckdb_path=tmp_path / "openpine.duckdb",
    )


def _state(strategy_id: str = "strategy-1", bar_time: int = 1, state_data=None) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={
            "exchange": "binance",
            "market": "spot",
            "symbol": "BTCUSDT",
            "price_type": "trade",
        },
        timeframe={"canonical": "15m"},
        state_data=state_data if state_data is not None else {"value": bar_time},
        bar_time=bar_time,
        saved_at=bar_time + 10,
    )


def _snapshot_meta(strategy_id: str, snapshot_id: str, *, bar_time: int = 1) -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id=snapshot_id,
        strategy_id=strategy_id,
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={"exchange": "binance"},
        timeframe={"canonical": "15m"},
        bar_time=bar_time,
        saved_at=bar_time + 100,
        size_bytes=1,
        state_encoding="msgpack",
    )


def _strategy(
    strategy_id: str = "strategy-1",
    *,
    pine_id: str = "pine-1",
    artifact_id: str = "artifact-1",
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    exchange: str = "binance",
    market_type: str = "spot",
    price_type: str = "trade",
    mode: str = "paper",
    enabled: bool = True,
    params_json: str = '{"length": 20}',
) -> StrategyInstance:
    return StrategyInstance(
        strategy_id=strategy_id,
        name=strategy_id,
        pine_id=pine_id,
        artifact_id=artifact_id,
        params_json=params_json,
        params_hash="params-1",
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        market_type=market_type,
        price_type=price_type,
        mode=mode,
        enabled=enabled,
    )


def _bar(open_time: int = 0, *, timeframe: str = "15m", symbol: str = "BTCUSDT") -> Bar:
    tf = parse_timeframe(timeframe)
    minute = open_time / 60_000
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol=symbol),
        timeframe=tf,
        time=open_time,
        time_close=open_time + (tf.duration_ms or 0),
        open=100.0 + minute,
        high=110.0 + minute,
        low=90.0 + minute,
        close=105.0 + minute,
        volume=42.0,
        closed=True,
    )


def _job_input(bar: Bar) -> dict:
    return {
        "strategy_id": "strategy-1",
        "artifact_id": "artifact-1",
        "params_hash": "params-1",
        "instrument_key": "binance:spot:BTCUSDT:trade",
        "timeframe": bar.timeframe.canonical,
        "bar_time": bar.time,
        "bar_close_time": bar.time_close,
    }


def _storage(tmp_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def _backtest_request(strategy_id: str = "strategy-1") -> BacktestRunRequest:
    return BacktestRunRequest(
        strategy_id=strategy_id,
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="15m",
        from_time=1,
        to_time=2,
        warmup_bars=3,
    )


def test_storage_adapters_leftover_health_paths(monkeypatch, tmp_path: Path) -> None:
    from openpine._compat import parquet

    lake_dir = tmp_path / "lake"
    lake_dir.mkdir()
    latest = lake_dir / "BTCUSDT_1m_2026-06-12.parquet"
    latest.write_bytes(b"placeholder")
    parquet_reads: list[Path] = []
    monkeypatch.setattr(
        parquet,
        "read_dataframe",
        lambda path: parquet_reads.append(Path(path)) or SimpleNamespace(),
    )
    parquet_adapter = ParquetDataLakeAdapter(data_dir=lake_dir)
    parquet_adapter._pyarrow_available = False

    parquet_info = parquet_adapter.health_check()

    assert parquet_info.health == BackendHealth.AVAILABLE
    assert parquet_reads == [latest]
    assert parquet_info.extra["schema"] == "pandas-fallback"

    class _DuckConn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str, params=()):
            self.sql.append(sql)
            if "glob(" in sql:
                raise RuntimeError("glob failed")
            return SimpleNamespace(fetchone=lambda: (1,))

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name in {"duckdb", "psycopg"}:
            raise ImportError(f"blocked {name}")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as ctx:
        ctx.setattr(builtins, "__import__", blocked_import)
        duck_adapter = DuckDBAnalyticsAdapter(
            db_path=tmp_path / "analytics.duckdb", data_dir=lake_dir
        )
        duck_adapter._duckdb_available = True
        duck_adapter._conn = _DuckConn()

        duck_info = duck_adapter.health_check()

        assert duck_info.health == BackendHealth.AVAILABLE
        assert duck_info.version == "unknown"
        assert duck_info.extra["parquet_files"] is None
        assert duck_info.extra["parquet_files_error"] == "glob failed"

        pg_adapter = PostgresControlStorageAdapter(
            host="localhost", port=5432, dbname="openpine", user="u", password="p"
        )
        pg_adapter._psycopg_available = True

        def boom() -> str | None:
            raise RuntimeError("health boom")

        pg_adapter._health_check_impl = boom  # type: ignore[method-assign]
        assert pg_adapter.available() is False
        pg_info = pg_adapter.health_check()
        assert pg_info.health == BackendHealth.UNAVAILABLE_ERROR
        assert pg_info.error == "health boom"

        missing_pg = PostgresControlStorageAdapter(
            host="localhost", port=5432, dbname="openpine", user="u", password="p"
        )
        assert missing_pg._health_check_impl() == "psycopg not installed"


def test_state_store_on_request_save_and_snapshot_cleanup(monkeypatch, tmp_path: Path) -> None:
    on_request_store = StateStore(tmp_path / "on_request", save_policy=SavePolicy.ON_REQUEST)
    saved = on_request_store.save_snapshot(_state("on-request", 10), reason="manual")

    assert saved is not None
    assert on_request_store.load_snapshot("on-request").state_data == {"value": 10}

    failing_store = StateStore(tmp_path / "failing")
    original_replace = Path.replace
    temp_paths: list[Path] = []

    def fail_snapshot_replace(self: Path, target: Path) -> Path:
        if str(target).endswith(".state.msgpack.zst"):
            temp_paths.append(Path(self))
            raise RuntimeError("snapshot rename failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_snapshot_replace)

    with pytest.raises(RuntimeError, match="snapshot rename failed"):
        failing_store.save_snapshot(_state("atomic", 20))

    assert temp_paths and not temp_paths[0].exists()
    assert list((tmp_path / "failing" / "strategy_id=atomic").glob("*.tmp")) == []


def test_state_store_missing_snapshot_bad_checksum_and_index_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    store = StateStore(tmp_path / "state")
    missing_meta = _snapshot_meta("missing", "does-not-exist", bar_time=1)
    store._snapshots["missing"].append(missing_meta)

    assert store.load_snapshot("missing") is None

    bad_meta = _snapshot_meta("bad", "checksum", bar_time=2)
    bad_payload = _state("bad", 2).to_payload()
    bad_payload["checksum"] = "not-the-packed-sha"
    packed = msgpack.packb(bad_payload, use_bin_type=True)
    bad_path = store._snapshot_path("bad", "checksum")
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(packed)
    bad_meta.size_bytes = len(packed)

    with pytest.raises(InvalidSnapshotError, match="Checksum mismatch"):
        store._load_state(bad_meta)

    store._snapshots["bad"].append(bad_meta)
    original_replace = Path.replace
    index_temps: list[Path] = []

    def fail_index_replace(self: Path, target: Path) -> Path:
        if Path(target).name == "snapshots.index.json":
            index_temps.append(Path(self))
            raise RuntimeError("index rename failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_index_replace)

    with pytest.raises(RuntimeError, match="index rename failed"):
        store._persist_metadata_index()

    assert index_temps and not index_temps[0].exists()
    assert list((tmp_path / "state").glob("*.index.tmp")) == []


def test_sqlite_strategy_registry_default_path_delete_missing_optional_tables(
    monkeypatch, tmp_path: Path
) -> None:
    import openpine.config as config_pkg
    import openpine.registry.strategies as strategies_mod

    cfg = SimpleNamespace(sqlite_path=tmp_path / "registry.sqlite", data_dir=tmp_path / "data")
    monkeypatch.setattr(strategies_mod, "DEFAULT_CONFIG", cfg)
    monkeypatch.setattr(config_pkg, "DEFAULT_CONFIG", cfg)

    registry = SQLiteStrategyRegistry()
    try:
        conn = registry._storage()
        conn.execute("CREATE TABLE backtest_trades (strategy_id TEXT)")
        conn.execute("CREATE TABLE backtest_artifacts (strategy_id TEXT)")
        conn.execute("CREATE TABLE backtest_runs (strategy_id TEXT)")
        conn.commit()

        strategy = registry.create_strategy(
            name="delete-me",
            pine_id="pine-1",
            artifact_id="artifact-1",
            symbol="BTCUSDT",
            timeframe="15m",
        )
        backtest_dir = cfg.data_dir / "backtests" / strategy.strategy_id
        backtest_dir.mkdir(parents=True)
        (backtest_dir / "artifact.txt").write_text("x", encoding="utf-8")

        registry.delete_strategy(strategy.strategy_id)

        assert not backtest_dir.exists()
        with pytest.raises(KeyError):
            registry.get_strategy(strategy.strategy_id)
    finally:
        registry.close()


def test_backup_restore_verify_load_defaults_and_missing_paths(
    monkeypatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    monkeypatch.setattr(
        backup_mod.OpenPineConfig,
        "load",
        classmethod(lambda cls, config_path=None: cfg),
    )

    archive = tmp_path / "backup.tar.gz"
    backed_up = backup_mod.backup_openpine(archive)

    assert archive.exists()
    assert backed_up == [str(cfg.config_path())]

    backup_mod.restore_openpine(archive)
    assert (cfg.data_dir / "config" / "manifest.json").exists()

    verified = backup_mod.verify_openpine()
    assert verified["sqlite_exists"] is False
    assert verified["sqlite_integrity"] is False
    assert verified["duckdb_exists"] is False
    assert verified["config_exists"] is False


def test_migration_file_filter_and_schema_helpers(tmp_path: Path) -> None:
    assert migrations_mod._get_migration_files(tmp_path / "missing") == []

    (tmp_path / "subdir").mkdir()
    (tmp_path / "002_ignore.txt").write_text("-- no", encoding="utf-8")
    sql_path = tmp_path / "001_init_schema.sql"
    sql_path.write_text("SELECT 1;", encoding="utf-8")

    assert migrations_mod._get_migration_files(tmp_path) == [
        (1, "init_schema", sql_path)
    ]
    with pytest.raises(ValueError, match="Unsafe SQLite identifier"):
        schema_compat._quote_identifier("bad-name")
    assert schema_compat.row_dict((1, "two"), ("one", "name")) == {
        "one": 1,
        "name": "two",
    }


def test_backtest_list_all_runs_and_strategy_ledger_status_filters(
    monkeypatch, tmp_path: Path
) -> None:
    cfg = _config(tmp_path)
    monkeypatch.setattr(
        OpenPineConfig,
        "load",
        classmethod(lambda cls, config_path=None: cfg),
    )

    with _storage(tmp_path) as storage:
        store = BacktestResultStore(storage)
        run_id = store.create_run(_backtest_request("strategy-1"))

        assert [run.run_id for run in store.list_all_runs(limit=5)] == [run_id]

        ledger = StrategyLedger(storage)
        ledger.record_trade(
            StrategyTrade(
                trade_id="trade-open",
                strategy_id="strategy-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
                source=LedgerSource.PAPER,
                status=TradeStatus.OPEN,
                direction="long",
                entry_time=1,
                entry_price=100.0,
                qty=0.1,
            )
        )
        ledger.record_trade(
            StrategyTrade(
                trade_id="trade-closed",
                strategy_id="strategy-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
                source=LedgerSource.PAPER,
                status=TradeStatus.CLOSED,
                direction="long",
                entry_time=2,
                entry_price=100.0,
                qty=0.2,
                exit_time=3,
                exit_price=101.0,
            )
        )

        assert ledger_mod.generate_trade_id().startswith("trade_")
        assert [
            trade.trade_id for trade in ledger.list_trades(status=TradeStatus.OPEN)
        ] == ["trade-open"]


def test_strategy_job_executor_load_bar_runtime_and_ledger_leftovers(
    monkeypatch, tmp_path: Path
) -> None:
    bar = _bar(0)
    strategy = _strategy()
    scheduler = JobScheduler()

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        orchestrator=SimpleNamespace(get_bars=lambda query: []),
        scheduler=scheduler,
        state_store=StateStore(tmp_path / "state"),
        runtime_adapter=SimpleNamespace(run=lambda *args, **kwargs: None),
        strategy_loader=lambda loaded_strategy: _DummyStrategy,
    )

    with pytest.raises(RuntimeError, match="expected one stored 15m bar"):
        executor._load_target_bar(strategy, _job_input(bar))

    provider_calls: list[dict] = []

    def fake_provider(**kwargs):
        provider_calls.append(kwargs)
        return "local-provider"

    class _RuntimeAdapter:
        def __init__(self) -> None:
            self.calls = []

        def run(self, strategy_class, bars, config, **kwargs):
            self.calls.append((strategy_class, bars, config, kwargs))
            return SimpleNamespace(status="completed")

    runtime_adapter = _RuntimeAdapter()
    monkeypatch.setattr(
        job_exec_mod, "create_local_runtime_data_provider_adapter", fake_provider
    )
    executor.runtime_adapter = runtime_adapter
    executor.runtime_data_provider = None

    result = executor._run_strategy(strategy, bar, resume_state={"resume": True})

    assert result.status == "completed"
    assert provider_calls == [
        {
            "exchange": "binance",
            "market": "spot",
            "prefetch_end_ms": bar.time_close,
        }
    ]
    assert getattr(_DummyStrategy, "runtime_data_provider") == "local-provider"
    assert runtime_adapter.calls[0][3]["runtime_data_provider"] == "local-provider"
    assert runtime_adapter.calls[0][3]["resume_state"] == {"resume": True}
    assert executor._record_ledger(
        strategy,
        Job(job_type=JobType.PAPER_BAR_PROCESS, strategy_id=strategy.strategy_id),
        bar,
        SimpleNamespace(raw_result=SimpleNamespace()),
    ) == 0

    executor._record_position(
        strategy,
        LedgerSource.PAPER,
        bar,
        resume_state=None,
        raw_result=SimpleNamespace(open_trades=[]),
    )


def test_strategy_job_executor_artifact_helpers(monkeypatch) -> None:
    monkeypatch.setattr(
        job_exec_mod,
        "load_strategy_class_from_artifact",
        lambda pine_id, artifact_id, **kwargs: (pine_id, artifact_id, kwargs),
    )

    loaded = job_exec_mod._load_strategy_class(_strategy())

    assert loaded == (
        "pine-1",
        "artifact-1",
        {"symbol": "BTCUSDT", "timeframe": "15m"},
    )
    assert job_exec_mod._artifact_declaration_args(_strategy(pine_id="")) == {}

    class _ArtifactStore:
        def get_artifact(self, artifact_id: str, pine_id: str):
            return {
                "compile_meta": {
                    "translation_metadata": {
                        "declaration": {
                            "arguments": {"initial_capital": 1234.0, "pyramiding": 2}
                        }
                    }
                }
            }

    monkeypatch.setattr("openpine.artifacts.ArtifactStore", lambda: _ArtifactStore())

    assert job_exec_mod._artifact_declaration_args(_strategy()) == {
        "initial_capital": 1234.0,
        "pyramiding": 2,
    }


class _FanoutRegistry:
    def __init__(self, strategies=()) -> None:
        self.strategies = tuple(strategies)

    def list_strategies(self):
        return list(self.strategies)


class _FanoutOrchestrator:
    def __init__(self) -> None:
        self.closed = []
        self.bars: dict[tuple[int, str], Bar] = {}

    def on_candle_closed(
        self, bar: Bar, *, instrument_key: str, timeframe: str, source: str
    ) -> None:
        self.closed.append((bar, instrument_key, timeframe, source))
        self.bars[(bar.time, timeframe)] = bar

    def get_bars(self, query: BarQuery):
        step = query.timeframe.duration_ms or 0
        current = query.start_ms
        out = []
        while current < query.end_ms:
            found = self.bars.get((current, query.timeframe.canonical))
            if found is not None:
                out.append(found)
            current += step
        return out


def test_strategy_fanout_leftover_errors_empty_and_aggregation_paths() -> None:
    empty_fanout = StrategyBarFanout(
        registry=_FanoutRegistry(),
        orchestrator=_FanoutOrchestrator(),
        scheduler=JobScheduler(),
    )

    with pytest.raises(ValueError, match="expects 1m source bars"):
        empty_fanout.process_source_bar(_bar(0, timeframe="5m"))

    no_strategies = empty_fanout.process_source_bar(_bar(0, timeframe="1m"))
    assert no_strategies.strategies == 0
    assert no_strategies.targets[0].status == FanoutStatus.NO_STRATEGIES

    with pytest.raises(ValueError, match="variable duration timeframe"):
        empty_fanout._target_bar_from_source(_bar(0, timeframe="1m"), "1M")

    below_source = StrategyBarFanout(
        registry=_FanoutRegistry(),
        orchestrator=_FanoutOrchestrator(),
        scheduler=JobScheduler(),
        config=StrategyBarFanoutConfig(source_timeframe="5m"),
    )
    with pytest.raises(ValueError, match="below source"):
        below_source._target_bar_from_source(_bar(0, timeframe="5m"), "1m")

    not_multiple = StrategyBarFanout(
        registry=_FanoutRegistry(),
        orchestrator=_FanoutOrchestrator(),
        scheduler=JobScheduler(),
        config=StrategyBarFanoutConfig(source_timeframe="2m"),
    )
    with pytest.raises(ValueError, match="not a multiple"):
        not_multiple._target_bar_from_source(_bar(0, timeframe="2m"), "3m")

    assert empty_fanout._target_bar_from_source(_bar(14 * 60_000, timeframe="1m"), "15m") is None

    with pytest.raises(ValueError, match="cannot aggregate empty"):
        fanout_mod._aggregate_bars([], target_timeframe="1m")
