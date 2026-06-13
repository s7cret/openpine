from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    CoverageReport,
    FootprintQuery,
    FootprintSeries,
    InstrumentKey,
    StoreResult,
    parse_timeframe,
)

from openpine.batch import tv_corpus as tv
from openpine.cli import runtime_helpers as rh
from openpine.compile import adapter as ca
from openpine.data.footprint_orchestrator import FootprintOrchestrator
from openpine.data.orchestrator import IncompleteCoverageError, StorageUnavailableError
from openpine.gateway import server


class Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *parts, **kwargs):
        self.lines.append(" ".join(str(p) for p in parts))


class _Registry:
    def __init__(self):
        self.statuses: list[tuple[str, str]] = []

    def update_status(self, strategy_id: str, status: str) -> None:
        self.statuses.append((strategy_id, status))


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close, close, close, 1.0, True)


def test_tv_corpus_manifest_timeframes_cache_and_filters(tmp_path: Path):
    root = tmp_path / "clean"
    export = root / "exports" / "folder_a"
    export.mkdir(parents=True)
    (export / "source.pine").write_text("strategy('x')\n", encoding="utf-8")
    rows = "time,open,high,low,close,Volume\n0,1,2,0.5,1.5,10\n60000,2,3,1,2.5,11\n"
    (export / "BTCUSDT_15m.csv").write_text(rows, encoding="utf-8")
    (export / "ETHUSDT_15m.csv").write_text(rows, encoding="utf-8")
    manifest = root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "7,folder_a,strategy,grp,source.pine, BTCUSDT_15m.csv|ETHUSDT_15m.csv,1m|15m\n".replace(
            ", BTC", ",BTC"
        ),
        encoding="utf-8",
    )

    # Explicit name, inferred diff, and unknown timeframe branches.
    assert tv.timeframe_from_name(Path("coin, 15.csv")) == "15m"
    assert tv.timeframe_from_name(Path("coin_1h.csv")) == "1h"
    assert tv.timeframe_from_name(Path("coin_1d.csv")) == "1D"
    assert tv.timeframe_from_name(Path("coin_5m.csv")) is None
    assert tv.normalize_tf("15min") == "15m"
    assert tv.normalize_tf("60m") == "1h"
    assert tv.normalize_tf("d") == "1D"
    assert tv.normalize_tf("2h") == "2h"
    assert tv.sanitize_name(" !!! ") == "unnamed"

    entries = tv.load_manifest(manifest, root)
    assert len(entries) == 1
    entry = entries[0]
    assert tv.openpine_name(entry).startswith("po_0007")
    assert tv.strategy_name(entry, "15m").endswith("15m")
    assert tv.filter_entries(entries, kind="indicator", timeframe=None, limit=None, start_id=None, only_id=None) == []
    assert tv.filter_entries(entries, kind="all", timeframe="15min", limit=1, start_id=7, only_id={7}) == entries
    assert tv.filter_entries(entries, kind="all", timeframe="1h", limit=None, start_id=None, only_id=None) == []

    bars, meta = tv.load_visible_bars_by_time(
        root=root,
        manifest=manifest,
        source_group="grp",
        timeframe="15m",
        symbol="BTCUSDT",
    )
    assert bars and meta["unique_bars"] == 2
    cached, cached_meta = tv.load_visible_bars_by_time(
        root=root,
        manifest=manifest,
        source_group="grp",
        timeframe="15m",
        symbol="BTCUSDT",
    )
    assert cached == bars and cached_meta["unique_bars"] == 2

    merged, patched = tv.merge_visible_bars(provider_bars=[_bar(0, 99), _bar(120_000, 7)], visible_bars_by_time=bars)
    assert patched == 1 and merged[0].close == bars[0]["close"] and merged[1].close == 7
    assert tv.merge_visible_bars(provider_bars=[], visible_bars_by_time={}) == ([], 0)


def test_tv_corpus_error_and_conflict_edges(tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("time,open\n0,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing OHLC"):
        tv.read_chart(bad)
    empty = tmp_path / "empty.csv"
    empty.write_text("time,open,high,low,close\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        tv.read_chart(empty)
    unknown = tmp_path / "unknown.csv"
    unknown.write_text("time,open,high,low,close\n0,1,1,1,1\n1000,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot infer"):
        tv.build_chart_export(unknown)

    root = tmp_path / "root"
    export = root / "exports" / "f"
    export.mkdir(parents=True)
    (export / "source.pine").write_text("indicator('x')\n", encoding="utf-8")
    (export / "BTCUSD_A.csv").write_text("time,open,high,low,close\n0,1,1,1,1\n", encoding="utf-8")
    (export / "BTCUSD_B.csv").write_text("time,open,high,low,close\n0,2,2,2,2\n", encoding="utf-8")
    manifest = root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "1,f,indicator,grp,source.pine,BTCUSD_A.csv|BTCUSD_B.csv,15m\n",
        encoding="utf-8",
    )
    bars, meta = tv.load_visible_bars_by_time(root=root, manifest=manifest, source_group="grp", timeframe="15m", symbol="BTCUSD")
    assert len(bars) == 1 and meta["conflicts"] == 1
    assert tv._chart_filename_can_match_symbol(Path("BTCUSD.csv"), "ETHUSD") is False

    missing_manifest = tmp_path / "none.csv"
    with pytest.raises(FileNotFoundError):
        tv.load_manifest(missing_manifest, root)
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "1,f,indicator,missing.pine,BTCUSD_A.csv,1m\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        tv.load_manifest(manifest, root)
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "1,f,indicator,grp,source.pine,,15m\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no chart"):
        tv.load_manifest(manifest, root)


def test_footprint_orchestrator_provider_store_and_failures():
    inst = InstrumentKey("binance", "spot", "BTCUSDT")
    tf = parse_timeframe("1m")
    query = FootprintQuery(inst, tf, 0, 60_000, price_bucket=1.0, source="auto", gap_policy="allow_with_metadata")
    complete = CoverageReport(0, 60_000, 0, 60_000)
    incomplete = CoverageReport(0, 60_000, None, None, missing_intervals=((0, 60_000),))
    series = FootprintSeries(query, (), complete)

    class Provider:
        def __init__(self, s):
            self.s = s
        def fetch_footprint(self, q):
            return self.s

    class Store:
        def __init__(self, s=series, result=None):
            self.s = s
            self.result = result or StoreResult(True, 0, None)
            self.reads = []
            self.writes = []
        def read(self, q):
            self.reads.append(q)
            return self.s
        def write(self, s):
            self.writes.append(s)
            return self.result

    assert FootprintOrchestrator(provider=Provider(series)).load_footprints(query) is series
    store = Store()
    assert FootprintOrchestrator(store=store).load_footprints(FootprintQuery(inst, tf, 0, 60_000, price_bucket=1.0, source="storage", gap_policy="allow_with_metadata")) is series
    with pytest.raises(StorageUnavailableError):
        FootprintOrchestrator().load_footprints(query)
    with pytest.raises(IncompleteCoverageError):
        FootprintOrchestrator(provider=Provider(FootprintSeries(query, (), incomplete))).load_footprints(FootprintQuery(inst, tf, 0, 60_000, price_bucket=1.0, source="auto", gap_policy="fail"))
    with pytest.raises(IncompleteCoverageError):
        FootprintOrchestrator(store=Store(FootprintSeries(query, (), incomplete))).load_footprints(FootprintQuery(inst, tf, 0, 60_000, price_bucket=1.0, source="storage", gap_policy="fail"))
    with pytest.raises(StorageUnavailableError):
        FootprintOrchestrator(store=Store(result=StoreResult(False, 0, "bad"))).store_footprints(series)


def test_compile_adapter_edges(monkeypatch, tmp_path: Path):
    assert ca._is_visual_contract_diagnostic("P2A1507 Builtin plot has no runtime-equivalent visual output")
    assert not ca._is_visual_contract_diagnostic("P2A1507 Builtin request.foo unsupported")
    assert ca._unsupported_request_in_source_error("x=request.financial('AAPL')") == "unsupported request call is not production lowerable: request.financial"
    assert ca._unsupported_request_in_source_error("x=request.security('BTC','1D',close)") is None
    assert ca._normalize_pine_v5_directive("//@version=5\nplot(close)") == ("//@version=6\nplot(close)", True)
    assert ca._normalize_pine_v5_directive("plot(close)") == ("plot(close)", False)
    assert ca._is_pine_v5_version_rejection(["P2A0103 unsupported Pine version 5"])
    assert not ca._is_pine_v5_version_rejection(["P2A0103 unsupported Pine version 5", "P2A9999 bad"])
    assert ca._production_metadata_blockers({"unsafe": True, "compile_profile": "diagnostic", "import_aliases": ["x"], "unsupported_features": ["u"]})
    mod = ModuleType("m")
    mod.__version__ = "1.2"
    assert ca._version_from_module(mod, "missing", "__version__") == "1.2"
    assert ca._version_from_module(ModuleType("empty"), "__version__") == "unknown"
    monkeypatch.setattr(ca.shutil, "which", lambda name: None)
    monkeypatch.setattr(ca, "TOOL_SEARCH_PATHS", [tmp_path])
    assert ca._find_tool("nope") is None

    adapter = ca.SubprocessCompilerAdapter(prefer_library=True, fallback_to_subprocess=False)
    monkeypatch.setattr(ca, "_load_library_apis", lambda: (None, ca.LibraryAvailability(False, errors=["missing"], paths={}, versions={})))
    result = adapter.compile("//@version=6\nindicator('x')")
    assert not result.success and "missing" in result.errors
    assert not ca.SubprocessCompilerAdapter(prefer_library=False).compile("x").success
    assert not ca.SubprocessCompilerAdapter(prefer_library=True).compile("x", allow_invalid_ast=True).success


def test_runtime_helper_error_edges(monkeypatch):
    console = Console()
    registry = _Registry()
    strategy = SimpleNamespace(strategy_id="s1", timeframe="15m", params_json="{}", artifact_id="a", params_hash="h", exchange="binance", market_type="spot", symbol="BTCUSDT")
    with pytest.raises(SystemExit):
        rh._prepare_strategy_replay_inputs(
            strategy=strategy,
            strategy_id="s1",
            from_date="2024-01-02",
            to_date="2024-01-01",
            now_ms=10,
            registry=registry,
            load_strategy_class=lambda s: object,
            artifact_error_cls=RuntimeError,
            artifact_store_cls=lambda: SimpleNamespace(get_artifact=lambda artifact_id: None),
            bar_query_cls=object,
            instrument_key_cls=object,
            parse_timeframe_func=lambda value: SimpleNamespace(duration_ms=60_000),
            orchestrator_cls=object,
            config_cls=object,
            perf_counter=lambda: 0.0,
            console=console,
        )
    assert registry.statuses[-1] == ("s1", "paused")

    console = Console()
    rh._print_strategy_plot_capture_status(raw_result=SimpleNamespace(plots=None), capture_plots=True, console=console)
    rh._print_strategy_plot_capture_status(raw_result=SimpleNamespace(plots=[]), capture_plots=True, console=console)
    rh._print_strategy_plot_capture_status(raw_result=SimpleNamespace(plots=[{"x": 1}]), capture_plots=True, console=console)
    rh._print_strategy_plot_capture_status(raw_result=SimpleNamespace(plots=SimpleNamespace(get_records=lambda: [])), capture_plots=True, console=console)
    assert any("unavailable" in line or "empty" in line or "captured" in line for line in console.lines)

    # _save_strategy_resume_snapshot: no state is a fast return; failures are warning-only.
    rh._save_strategy_resume_snapshot(strategy=strategy, prepared=SimpleNamespace(bars=[], end_ms=123), result=SimpleNamespace(resume_state=None), console=console)
    class BadStore:
        def __init__(self, *a, **k):
            pass
        def save_runtime_snapshot(self, **kwargs):
            raise RuntimeError("snap boom")
    monkeypatch.setitem(sys.modules, "openpine.state.store", ModuleType("openpine.state.store"))
    sys.modules["openpine.state.store"].StateStore = BadStore
    rh._save_strategy_resume_snapshot(strategy=strategy, prepared=SimpleNamespace(bars=[], end_ms=123), result=SimpleNamespace(resume_state={}), console=console)


def test_gateway_server_background_and_env_edges(monkeypatch):
    assert server._env_flag("NO_SUCH_OPENPINE_FLAG", True) is True
    monkeypatch.setenv("OPENPINE_TEST_FLAG", "off")
    assert server._env_flag("OPENPINE_TEST_FLAG", True) is False
    monkeypatch.setenv("OPENPINE_TEST_FLAG", "YES")
    assert server._env_flag("OPENPINE_TEST_FLAG") is True

    calls = []
    class Fetcher:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            calls.append("fetch-start")
        def stop(self):
            calls.append("fetch-stop")
    class Runner:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            calls.append("run-start")
        def stop(self):
            calls.append("run-stop")
    state = SimpleNamespace(strategy_registry=None, orchestrator=None, storage=None, artifact_store=None, state_store=None, close=lambda: calls.append("state-close"))
    monkeypatch.setattr("openpine.gateway.server.GatewayState", lambda: state)
    monkeypatch.setattr("openpine.data.periodic_fetcher.PeriodicBarFetcher", Fetcher)
    monkeypatch.setattr("openpine.gateway.live_runner.LiveStrategyRunner", Runner)
    class Stop:
        def __init__(self):
            self.n = 0
        def is_set(self):
            self.n += 1
            return self.n > 1
    async def fake_sleep(_):
        return None
    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    server._run_background_services(Stop())
    assert calls == ["fetch-start", "run-start", "run-stop", "fetch-stop", "state-close"]
