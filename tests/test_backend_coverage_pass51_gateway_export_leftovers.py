from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pandas as pd
import pytest
from fastapi import HTTPException

from openpine.export import ExportWindow, export_strategy_result, parse_time_ms
from openpine.export.plots import export_plot_records
from openpine.gateway import server
from openpine.gateway.config import GatewayConfig
from openpine.gateway.routes import dashboard, pine_sources, strategies
from openpine.gateway.schemas import CompareTvRequest


class _Cursor:
    def __init__(self, *, rows=(), one=None):
        self._rows = list(rows)
        self._one = one

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_lifespan_logs_stuck_backtest_cleanup_failure_and_stops_fake_worker(monkeypatch):
    """Exercise startup cleanup exception handling without launching real workers."""

    class FailingStorage:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def execute(self, sql, params=()):
            self.queries.append(str(sql))
            raise RuntimeError("backtest table is unavailable")

        def commit(self):  # pragma: no cover - cleanup exception skips commits
            raise AssertionError("commit should not run after execute failure")

    class FakeGatewayState:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                sqlite_path=Path("/tmp/openpine-test.sqlite"), live_enabled=False
            )
            self.storage = FailingStorage()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeStopEvent:
        def __init__(self) -> None:
            self.was_set = False

        def set(self) -> None:
            self.was_set = True

    class FakeProcess:
        instances: list["FakeProcess"] = []

        def __init__(self, *, target, args, name, daemon) -> None:
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.pid = 4242
            self.started = False
            self.join_timeouts: list[object] = []
            self.terminated = False
            FakeProcess.instances.append(self)

        def start(self) -> None:
            self.started = True

        def join(self, timeout=None) -> None:
            self.join_timeouts.append(timeout)

        def is_alive(self) -> bool:
            return not self.terminated

        def terminate(self) -> None:
            self.terminated = True

    class FakeContext:
        def __init__(self) -> None:
            self.stop_event = FakeStopEvent()

        def Event(self) -> FakeStopEvent:
            return self.stop_event

        Process = FakeProcess

    fake_context = FakeContext()
    requested_methods: list[str] = []
    monkeypatch.setattr(server, "GatewayState", FakeGatewayState)
    monkeypatch.setattr(
        server.mp,
        "get_context",
        lambda method: requested_methods.append(method) or fake_context,
    )
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "0")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "0")

    app: Any = SimpleNamespace(state=SimpleNamespace())
    async with server.lifespan(cast(Any, app)):
        assert app.state.gateway.storage.queries[0].startswith("SELECT run_id")
        assert FakeProcess.instances[0].started is True
        assert app.state.gateway._background_worker_process is FakeProcess.instances[0]

    proc = FakeProcess.instances[0]
    assert requested_methods == ["spawn"]
    assert fake_context.stop_event.was_set is True
    assert proc.terminated is True
    assert proc.join_timeouts == [10, 5]
    assert app.state.gateway.closed is True


@pytest.mark.asyncio
async def test_create_app_health_and_root_routes_return_versions() -> None:
    app = server.create_app(GatewayConfig(api_prefix="/unit-api", cors_origins=["*"]))
    endpoints = {
        getattr(route, "path", None): getattr(route, "endpoint") for route in app.routes
    }

    assert await endpoints["/health"]() == {"status": "ok", "version": server.__version__}
    assert await endpoints["/"]() == {
        "service": "OpenPine Gateway",
        "version": server.__version__,
        "docs": "/docs",
        "api": "/unit-api",
    }


@pytest.mark.asyncio
async def test_strategy_compare_tv_skips_rows_with_unparseable_time(tmp_path: Path) -> None:
    openpine_csv = tmp_path / "openpine.csv"
    tv_csv = tmp_path / "tv.csv"
    openpine_csv.write_text(
        "time,bar_index,open,signal\nnot-a-time,9,99,99\n1000,0,1,2.5\n",
        encoding="utf-8",
    )
    tv_csv.write_text(
        "time,bar_index,open,signal\nbad-tv-time,8,88,88\n1000,0,9,2.5\n",
        encoding="utf-8",
    )

    result = await strategies.strategy_compare_tv(
        "strategy-x",
        CompareTvRequest(
            openpine_plots_path=str(openpine_csv),
            tv_chart_path=str(tv_csv),
            abs_tol=0.0,
            include_base_columns=False,
        ),
        state=cast(Any, SimpleNamespace()),
    )

    assert result["status"] == "match"
    assert result["classification"] == "exact"
    assert result["timestamps_compared"] == 1
    assert result["total_cells"] == 1


@pytest.mark.asyncio
async def test_delete_source_ignores_artifact_directory_cleanup_failure() -> None:
    class Registry:
        def __init__(self) -> None:
            self.removed: list[str] = []

        def get_source(self, source_id):
            if source_id != "pine-1":
                raise KeyError(source_id)
            return SimpleNamespace(id="pine-1")

        def remove_source(self, source_id) -> None:
            self.removed.append(source_id)

    class Storage:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple]] = []
            self.commits = 0

        def execute(self, sql, params=()):
            self.executed.append((str(sql), tuple(params)))
            return _Cursor()

        def commit(self) -> None:
            self.commits += 1

    registry = Registry()
    storage = Storage()
    state = SimpleNamespace(
        pine_registry=registry,
        storage=storage,
        artifact_store=SimpleNamespace(
            _source_dir=lambda source_id: (_ for _ in ()).throw(RuntimeError("no fs"))
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await pine_sources.delete_source("pine-1", state=cast(Any, state))

    assert exc.value.status_code == 500
    assert registry.removed == []
    assert storage.commits == 1
    assert len(storage.executed) == 2


@pytest.mark.asyncio
async def test_dashboard_last_bar_update_outer_fallback_exception_is_nonfatal(monkeypatch) -> None:
    class Registry:
        def __init__(self) -> None:
            self.calls = 0

        def list_strategies(self):
            self.calls += 1
            if self.calls == 1:
                return []
            raise RuntimeError("registry disappeared during fallback")

    class Storage:
        def execute(self, sql, params=()):
            return _Cursor()

    state = SimpleNamespace(
        strategy_registry=Registry(),
        scheduler=SimpleNamespace(list_jobs=lambda: []),
        storage=Storage(),
        orchestrator=SimpleNamespace(),
        _fetcher=None,
        _risk_kill_switch=[False],
        _startup_time=1_000.0,
    )
    monkeypatch.setattr(dashboard.time, "time", lambda: 1_001.0)

    response = await dashboard.dashboard(state=cast(Any, state))

    assert response.strategies == []
    assert response.last_bar_update is None
    assert response.uptime_seconds == 1.0


def test_export_plot_records_accepts_object_records_and_current_values(tmp_path: Path) -> None:
    output = tmp_path / "plots.csv"
    record = SimpleNamespace(
        bar_time=1_700_000_000_000,
        bar_index=7,
        value=SimpleNamespace(_current=12.5),
        title="ObjectPlot",
    )

    rows = export_plot_records([record], output)

    exported = pd.read_csv(output)
    assert rows == 1
    assert exported.to_dict("records") == [
        {"bar_time": 1_700_000_000_000, "bar_index": 0, "ObjectPlot": 12.5}
    ]
    assert not output.with_suffix(".long.tmp.csv").exists()


def test_export_strategy_result_handles_missing_plots_and_naive_time_parse(tmp_path: Path) -> None:
    exported = export_strategy_result(
        result=SimpleNamespace(plots=None, trades=[], equity_curve=[]),
        window=ExportWindow(1000, 2000),
        output_dir=tmp_path,
    )

    assert exported.plots_rows == 0
    assert exported.trades_rows == 0
    assert exported.all_trades_rows == 0
    assert exported.equity_rows == 0
    assert exported.initial_equity_at_export_start is None
    assert Path(exported.outputs["plots"]).exists()
    assert parse_time_ms("2024-01-01 00:00:00") == parse_time_ms("2024-01-01T00:00:00Z")
