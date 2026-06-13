from __future__ import annotations

import asyncio
import json
import shutil
import sys
import types
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from openpine.artifacts.store import ArtifactStore
from openpine.execution.ccxt_common import CcxtOrderClientMixin
from openpine.execution.paper import PaperExecutionAdapter
from openpine.gateway.routes import trading
from openpine.optimizer.adapter import LocalOptimizerAdapter, OptimizerRunConfig
from openpine.orders.models import Order, OrderIntent, OrderSide, OrderStatus, OrderType
from openpine.recovery.rebuild import StateRebuilder
from openpine.state.store import SnapshotMetadata, StateStore, StrategyState
from openpine.storage import backup as backup_mod
from openpine.storage import migrations as migrations_mod
from openpine.storage.adapters import (
    DuckDBAnalyticsAdapter,
    SQLiteControlStorageAdapter,
)
from openpine.storage.backtest_dto import (
    ARTIFACT_TYPE_PLOT_OUTPUTS,
    ARTIFACT_TYPE_REPORT_JSON,
    ARTIFACT_TYPE_REPORT_MD,
    BacktestMetricsSummary,
)
from openpine.storage.backtest_storage import BacktestResultStore


def _install_fake_pinelib_plot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep plot-record tests hermetic if the optional pinelib package is absent."""

    pinelib_module = types.ModuleType("pinelib")
    plot_module = types.ModuleType("pinelib.plot")

    class PlotRecorder:
        pass

    setattr(plot_module, "PlotRecorder", PlotRecorder)
    setattr(pinelib_module, "plot", plot_module)
    monkeypatch.setitem(sys.modules, "pinelib", pinelib_module)
    monkeypatch.setitem(sys.modules, "pinelib.plot", plot_module)


def test_optimizer_missing_engine_contract_and_empty_trial_counts() -> None:
    adapter = LocalOptimizerAdapter(optimizer_module=ModuleType("optimizer"))
    config = OptimizerRunConfig(
        strategy_id="strategy-1",
        trials=3,
        artifact_id="artifact-1",
        params_hash="hash-1",
        data_query={"symbol": "BTC/USDT"},
        parameters=({"name": "length", "default": 14},),
        engine_factory=None,
        strategy=object(),
        bars=(object(),),
    )

    result = adapter._run_local_optimizer("opt-contract", config)

    assert result.status == "failed"
    assert result.metrics["failure_reason"] == (
        "production optimization requires engine_factory, strategy, and bars"
    )

    counts = adapter._trial_status_counts(
        SimpleNamespace(trials_count_by_status={"failed": 2}), ()
    )
    assert counts == {"failed": 2, "completed": 0}


def test_artifact_listing_skips_plain_files(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    source_dir = tmp_path / "source-1"
    source_dir.mkdir()
    (source_dir / "not-an-artifact.txt").write_text("ignore me")

    assert store.list_artifacts("source-1") == []


class _OpenOrdersClient:
    async def fetch_open_orders(self):
        return [{"id": "raw-without-trackable-order-id"}]


class _CcxtProbe(CcxtOrderClientMixin):
    def __init__(self) -> None:
        self._client = _OpenOrdersClient()
        self._rules = {}
        self.tracked: list[tuple[str, str]] = []

    def _track_order_symbol(self, order_id: str, symbol: str) -> None:
        self.tracked.append((order_id, symbol))

    def _parse_client_response(self, response, symbol: str | None = None):
        raise AssertionError("not used by reconcile")

    def _response_to_order_status(self, response) -> Order:
        return Order(
            order_id="",
            client_order_id="client-1",
            strategy_id="strategy-1",
            account_id="account-1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=10.0,
            stop_price=None,
            status=OrderStatus.NEW,
        )


def test_ccxt_reconcile_does_not_track_unidentified_orders() -> None:
    probe = _CcxtProbe()

    orders = asyncio.run(probe._call_reconcile("account-1"))

    assert len(orders) == 1
    assert probe.tracked == []


def test_paper_get_orders_without_account_filter_returns_all_orders() -> None:
    adapter = PaperExecutionAdapter()
    intent = OrderIntent(
        client_order_id="client-1",
        strategy_id="strategy-1",
        account_id="paper-account",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.5,
    )

    filled = asyncio.run(adapter.submit_order(intent))

    assert adapter.get_orders() == [filled]


def test_storage_adapter_cached_and_empty_close_paths() -> None:
    sentinel = object()
    sqlite = object.__new__(SQLiteControlStorageAdapter)
    sqlite._storage = sentinel
    sqlite._db_path = Path("unused.sqlite")

    assert sqlite._get_storage() is sentinel

    sqlite._storage = None
    sqlite.close()
    assert sqlite._storage is None

    duck = object.__new__(DuckDBAnalyticsAdapter)
    duck._conn = None
    duck.close()
    assert duck._conn is None


def test_backtest_save_result_exception_keeps_missing_tmp_dir_missing(tmp_path: Path) -> None:
    store = object.__new__(BacktestResultStore)
    store._data_dir = tmp_path
    store._get_strategy_id = lambda run_id: "strategy-1"

    def raise_after_removing_tmp(**kwargs):
        shutil.rmtree(kwargs["tmp_dir"])
        raise RuntimeError("artifact writer failed after cleanup")

    store._write_result_artifacts = raise_after_removing_tmp

    with pytest.raises(RuntimeError, match="artifact writer failed"):
        store.save_result("run-1", SimpleNamespace(), trades=[])

    assert not (tmp_path / "strategy-1" / "run-1.tmp").exists()


class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DeleteStorage:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Transaction()

    def execute(self, sql: str, params: tuple = ()):
        self.statements.append((sql, params))
        return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)


def test_backtest_delete_run_succeeds_when_artifact_dir_absent(tmp_path: Path) -> None:
    store = object.__new__(BacktestResultStore)
    storage = _DeleteStorage()
    setattr(store, "_storage", storage)
    setattr(store, "get_run", lambda run_id: SimpleNamespace(strategy_id="strategy-1"))
    setattr(store, "_run_dir", lambda strategy_id, run_id: tmp_path / "does-not-exist")

    assert store.delete_run("run-1") is True
    assert len(storage.statements) == 3


def test_backtest_plot_records_skip_unknown_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pinelib_plot(monkeypatch)

    assert BacktestResultStore._plot_records([object()]) == []


def test_backtest_write_result_artifacts_omits_empty_plot_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_pinelib_plot(monkeypatch)
    store = object.__new__(BacktestResultStore)
    tmp_dir = tmp_path / "run.tmp"
    run_dir = tmp_path / "run"
    tmp_dir.mkdir()

    artifact_paths = store._write_result_artifacts(
        tmp_dir=tmp_dir,
        run_dir=run_dir,
        run_id="run-1",
        strategy_id="strategy-1",
        result=SimpleNamespace(symbol="BTC/USDT", timeframe="1m"),
        metrics=BacktestMetricsSummary(trades_total=0),
        equity_curve=None,
        trades=[],
        bar_outputs=None,
        plots=[object()],
        now=123,
    )

    assert set(artifact_paths) == {ARTIFACT_TYPE_REPORT_JSON, ARTIFACT_TYPE_REPORT_MD}
    assert ARTIFACT_TYPE_PLOT_OUTPUTS not in artifact_paths
    assert not (tmp_dir / "plot_outputs.parquet").exists()


def test_backup_directory_walk_and_redact_list_non_dict_items(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    nested_artifact_dir = data_dir / "artifacts" / "nested-dir"
    nested_artifact_dir.mkdir(parents=True)
    backup_path = tmp_path / "backup.tar.gz"

    class Config:
        def __init__(self, root: Path) -> None:
            self.sqlite_path = root / "openpine.sqlite"
            self.duckdb_path = root / "analytics.duckdb"
            self.data_dir = root

        def config_path(self) -> Path:
            return tmp_path / "config.yaml"

        def model_dump(self) -> dict[str, Any]:
            return {"accounts": [{"api_key": "secret"}, "leave-alone"]}

    backed_up = backup_mod.backup_openpine(backup_path, cast(Any, Config(data_dir)))

    assert backup_path.exists()
    assert str(data_dir / "artifacts") in backed_up

    payload = {"items": [{"password": "secret"}, "not-a-dict"]}
    backup_mod._redact_sensitive(payload)
    assert payload == {"items": [{"password": "<REDACTED>"}, "not-a-dict"]}


def test_migration_file_scan_skips_unmatched_sql_before_valid_file() -> None:
    class FakePath:
        def __init__(self, name: str) -> None:
            self.name = name
            self.suffix = ".sql"

        def is_file(self) -> bool:
            return True

    bad = FakePath("not_numbered.sql")
    good = FakePath("001_initial_schema.sql")

    class FakeDir:
        def is_dir(self) -> bool:
            return True

        def iterdir(self):
            return iter([bad, good])

    assert migrations_mod._get_migration_files(cast(Path, FakeDir())) == [
        (1, "initial_schema", good)
    ]


def test_state_store_supersede_loop_keeps_inactive_previous_metadata(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state-save")
    inactive = SnapshotMetadata(
        snapshot_id="old-snapshot",
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="hash-1",
        instrument_key={"symbol": "BTC/USDT"},
        timeframe={"timeframe": "1m"},
        bar_time=1,
        saved_at=1,
        size_bytes=0,
        status="invalid",
    )
    store._snapshots["strategy-1"].append(inactive)

    saved = store.save_snapshot(
        StrategyState(
            strategy_id="strategy-1",
            artifact_id="artifact-1",
            params_hash="hash-1",
            instrument_key={"symbol": "BTC/USDT"},
            timeframe={"timeframe": "1m"},
            state_data={"position": {}},
            bar_time=2,
            saved_at=2,
        )
    )

    assert saved is not None
    assert inactive.status == "invalid"
    assert [meta.status for meta in store._snapshots["strategy-1"]] == [
        "invalid",
        "active",
    ]


def test_state_store_ignores_index_entries_with_missing_snapshot_files(tmp_path: Path) -> None:
    state_dir = tmp_path / "state-index"
    state_dir.mkdir()
    missing = SnapshotMetadata(
        snapshot_id="missing-snapshot",
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="hash-1",
        instrument_key={},
        timeframe={},
        bar_time=1,
        saved_at=1,
        size_bytes=1,
    )
    (state_dir / "snapshots.index.json").write_text(
        json.dumps({"snapshots": [missing.to_dict()]})
    )

    store = StateStore(state_dir)

    assert store.list_snapshots("strategy-1") == []


def test_state_rebuild_without_data_orchestrator_saves_loaded_state() -> None:
    state = SimpleNamespace(
        strategy_id="strategy-1",
        instrument_key={"symbol": "BTC/USDT"},
        timeframe={"timeframe": "1m"},
        bar_time=10,
    )

    class Store:
        def __init__(self) -> None:
            self.saved: list[tuple[object, str, bool]] = []

        def list_snapshots(self, strategy_id: str):
            return [SimpleNamespace(status="active", bar_time=5)]

        def load_snapshot(self, strategy_id: str):
            return state

        def save_snapshot(self, state_obj, reason: str, failed_bar: bool):
            self.saved.append((state_obj, reason, failed_bar))
            return SimpleNamespace(snapshot_id="rebuilt")

    store = Store()
    rebuilt = StateRebuilder(cast(Any, store), data_orchestrator=None).rebuild(
        "strategy-1", from_bar_time=20, reason="manual"
    )

    assert rebuilt is state
    assert store.saved == [(state, "manual", False)]


def test_trading_status_handles_absent_snapshot() -> None:
    class Registry:
        def get_strategy(self, strategy_id: str):
            return SimpleNamespace(mode="paper", status="paused")

    state = SimpleNamespace(
        strategy_registry=Registry(),
        state_store=SimpleNamespace(load_snapshot=lambda strategy_id: None),
    )

    response = asyncio.run(trading.get_trading_status("strategy-1", cast(Any, state)))

    assert response.strategy_id == "strategy-1"
    assert response.last_bar_time is None
    assert response.position_qty is None
    assert response.position_side is None
