from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.batch import persistent_cache as batch_cache
from openpine.batch import runner as batch_runner
from openpine.batch import tv_corpus
from openpine.batch.tv_corpus import ChartExport, ExportEntry


def _query(
    start_ms: int = 0,
    end_ms: int = 120_000,
    *,
    source: str = "auto",
    gap_policy: str = "allow_with_metadata",
) -> BarQuery:
    return BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=start_ms,
        end_ms=end_ms,
        source=source,
        gap_policy=gap_policy,
    )


def _bar(time_ms: int, *, close: float = 1.0) -> Bar:
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        time=time_ms,
        time_close=time_ms + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=close,
        volume=10.0,
        closed=True,
    )


def _chart_file(path: Path, *, times: list[int] | None = None) -> None:
    times = times or [1]
    pd.DataFrame(
        {
            "time": times,
            "open": [1.0] * len(times),
            "high": [2.0] * len(times),
            "low": [0.5] * len(times),
            "close": [1.5] * len(times),
            "Volume": [100.0] * len(times),
        }
    ).to_csv(path, index=False)


def test_batch_runner_progress_registry_offset_and_entry_loop_arcs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batch_runner._write_progress(tmp_path, "batch-1", None, "plan", "running")
    progress = json.loads((tmp_path / "current_progress.json").read_text(encoding="utf-8"))
    assert "selected_count" not in progress
    assert "processed_count" not in progress
    assert "summary_by_timeframe" not in progress

    entry_root = tmp_path / "entry"
    entry_root.mkdir()
    chart = ChartExport(
        timeframe="1m",
        path=tmp_path / "unused.csv",
        bars=1,
        start_ms=0,
        end_ms=60_000,
    )
    entry = ExportEntry(
        export_id=7,
        folder="folder",
        kind="strategy",
        source_group="sg",
        root=entry_root,
        pine_path=tmp_path / "script.pine",
        charts=(chart,),
    )
    wanted_name = tv_corpus.strategy_name(entry, "1m")

    class Registry:
        closed = False

        def list_strategies(self) -> list[Any]:
            return [
                SimpleNamespace(name="other", strategy_id="other-id"),
                SimpleNamespace(name=wanted_name, strategy_id="existing-id"),
            ]

        def register_strategy(self, **_kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError("matching existing strategy should short-circuit registration")

        def update_status(self, *_args: Any) -> None:  # pragma: no cover
            raise AssertionError("not reached")

        def close(self) -> None:
            self.closed = True

    registry = Registry()
    monkeypatch.setattr(batch_runner, "load_strategy_registry", lambda: registry)
    strategy_id, created = batch_runner.ensure_strategy_instance(
        entry, SimpleNamespace(id="pine-id"), "artifact-id", "1m"
    )
    assert (strategy_id, created) == ("existing-id", False)
    assert registry.closed is True

    chart_path = tmp_path / "bar_index_chart.csv"
    pd.DataFrame(
        {
            "time": [1, 2, 3],
            "open": [1.0, 1.0, 1.0],
            "high": [2.0, 2.0, 2.0],
            "low": [0.5, 0.5, 0.5],
            "close": [1.5, 1.5, 1.5],
            "NOISY BAR INDEX": [5, 7, 8],
            "TV BAR INDEX": [5, 6, 7],
            "after_candidate": [9.0, 9.0, 9.0],
        }
    ).to_csv(chart_path, index=False)
    offset, meta = batch_runner._infer_tv_bar_index_offset(
        ChartExport("1m", chart_path, 3, 1_000, 3_000),
        [SimpleNamespace(time=0), SimpleNamespace(time=1_000), SimpleNamespace(time=2_000)],
    )
    assert offset == 4
    assert meta is not None and meta["status"] == "inferred"

    args = SimpleNamespace(root=tmp_path, phase="run", stop_on_error=False, timeframe=None)
    assert (
        batch_runner._run_selected_entries(
            args=args,
            selected=[],
            batch_id="batch-empty",
            library_revisions={},
            errors_path=tmp_path / "empty-errors.jsonl",
        )
        == []
    )

    monkeypatch.setattr(batch_runner, "completed_for_selection", lambda _entry, _args: False)

    def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(batch_runner, "run_entry", boom)
    results = batch_runner._run_selected_entries(
        args=args,
        selected=[entry],
        batch_id="batch-fatal",
        library_revisions={"openpine": "test"},
        errors_path=tmp_path / "fatal-errors.jsonl",
    )
    assert results[0]["status"] == "fatal_error"
    assert results[0]["batch_id"] == "batch-fatal"
    assert (entry_root / "openpine_outputs" / "openpine_batch_status.json").exists()


def test_batch_runner_main_qty_step_present_and_empty_calculation_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = SimpleNamespace(
        qty_step=0.01,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        manifest=tmp_path / "manifest.csv",
        root=tmp_path,
        phase="run",
        kind="all",
        timeframe=None,
        limit=None,
        start_id=None,
        ids=None,
        summary_name="summary.json",
        errors_name="errors.jsonl",
    )

    class Parser:
        def parse_args(self, _argv: list[str] | None) -> SimpleNamespace:
            return args

    monkeypatch.setattr(batch_runner, "_build_arg_parser", lambda: Parser())
    monkeypatch.setattr(
        batch_runner,
        "_default_qty_step",
        lambda *_args: (_ for _ in ()).throw(AssertionError("qty_step already set")),
    )
    monkeypatch.setattr(batch_runner, "load_manifest", lambda _manifest, _root: [])
    monkeypatch.setattr(batch_runner, "filter_entries", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(batch_runner, "resolve_calculation_to_by_timeframe", lambda *_args: {})
    monkeypatch.setattr(batch_runner, "_get_library_revisions", lambda: {})
    monkeypatch.setattr(batch_runner, "_run_selected_entries", lambda **_kwargs: [])
    monkeypatch.setattr(batch_runner, "_write_timeframe_summary_csv", lambda **_kwargs: None)
    monkeypatch.setattr(
        batch_runner,
        "_build_batch_summary_payload",
        lambda **_kwargs: {"summary": {"selected": 0}, "results": []},
    )

    assert batch_runner.main(["ignored"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"] == {"selected": 0}


def test_tv_corpus_visible_bars_and_batch_cache_remaining_arcs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="cannot infer timeframe"):
        tv_corpus.infer_timeframe(Path("mystery.csv"), pd.DataFrame({"bar_time": [123]}))

    fingerprint_file = tmp_path / "fingerprint.txt"
    fingerprint_file.write_text("fingerprint", encoding="utf-8")
    assert batch_cache.path_fingerprint([fingerprint_file])

    root = tmp_path / "corpus"
    export_root = root / "exports" / "case_001"
    export_root.mkdir(parents=True)
    _chart_file(export_root / "ETHUSDT_15.csv", times=[1])
    _chart_file(export_root / "fallback_15.csv", times=[2])
    manifest = root / "manifest.csv"
    manifest.write_text(
        "source_group,timeframes,folder,chart_csv_files\n"
        "sg,15m,case_001,ETHUSDT_15.csv|BTCUSD_15.csv|fallback_15.csv\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(batch_cache, "load_tv_corpus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        batch_cache,
        "save_tv_corpus",
        lambda _cache_dir, _key, _bars, meta: {**meta, "cache_hit": False},
    )
    bars, meta = tv_corpus.load_visible_bars_by_time(
        root=root,
        manifest=manifest,
        source_group="sg",
        timeframe="15m",
        symbol="ETHUSDT",
    )
    assert sorted(bars) == [1_000, 2_000]
    assert meta["charts_scanned"] == 2


class _Response:
    def __init__(self, payload: list[list[Any]]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def _klines(start_ms: int, count: int) -> list[list[Any]]:
    return [
        [start_ms + index * 60_000, "1", "2", "0.5", "1.5", "10"]
        for index in range(count)
    ]


def test_direct_binance_data_provider_exhausts_fixed_page_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.data import direct_data_provider

    calls: list[str] = []

    def fake_urlopen(req: Any, timeout: int) -> _Response:
        calls.append(getattr(req, "full_url", str(req)))
        return _Response(_klines((len(calls) - 1) * 60_000_000, 1000))

    monkeypatch.setattr(direct_data_provider.urllib.request, "urlopen", fake_urlopen)

    provider = direct_data_provider.DirectBinanceDataProvider(timeout=1)
    bars = provider.get_bars("btcusdt", "1", 0, 2_000_000_000, max_bars=7)
    assert len(calls) == 20
    assert len(bars) == 7
    assert bars[0].time == 0


def test_direct_binance_contract_provider_exhausts_fetch_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.data import direct_provider

    calls: list[str] = []

    def fake_urlopen(req: Any, timeout: int) -> _Response:
        calls.append(getattr(req, "full_url", str(req)))
        return _Response(_klines(0, 1000))

    monkeypatch.setattr(direct_provider.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(direct_provider, "cache_enabled_by_env", lambda: False)
    monkeypatch.setattr(
        direct_provider.DirectBinanceProvider,
        "estimate_pages",
        classmethod(lambda cls, query, **_kwargs: -4),
    )

    provider = direct_provider.DirectBinanceProvider()
    monkeypatch.setattr(provider, "effective_query", lambda query: (query, None))
    events: list[tuple[Any, ...]] = []
    series = provider.fetch_bars(
        _query(0, 2_000_000_000, source="provider"),
        progress_callback=lambda *event: events.append(event),
    )

    assert len(calls) == 1
    assert len(series.bars) == 1000
    assert events[-1][-1] == "fetch_done"


def test_data_orchestrator_skips_empty_provider_write_and_non_fail_missing_fetch() -> None:
    from openpine.data.orchestrator import DataOrchestrator

    query = _query(0, 120_000, source="auto", gap_policy="allow_with_metadata")
    storage_bar = _bar(0)

    class Store:
        writes = 0

        def read(self, requested: BarQuery) -> BarSeries:
            return BarSeries(
                query=requested,
                bars=(storage_bar,),
                coverage=CoverageReport(
                    requested.start_ms,
                    requested.end_ms,
                    storage_bar.time,
                    storage_bar.time_close,
                    ((60_000, 120_000),),
                    source_mix=("storage",),
                    status="gap",
                ),
            )

        def write(self, _series: BarSeries) -> SimpleNamespace:
            self.writes += 1
            return SimpleNamespace(success=True, manifest_id="written", error=None)

    class Provider:
        def fetch_bars(self, requested: BarQuery) -> BarSeries:
            return BarSeries(
                query=requested,
                bars=(),
                coverage=CoverageReport(
                    requested.start_ms,
                    requested.end_ms,
                    None,
                    None,
                    ((requested.start_ms, requested.end_ms),),
                    source_mix=("provider",),
                    status="empty",
                ),
            )

    store = Store()
    orchestrator = DataOrchestrator(provider=Provider(), store=store, cache_enabled=False)
    series = orchestrator.load_bars(query)
    assert [bar.time for bar in series.bars] == [0]
    assert store.writes == 0


def test_periodic_fetcher_stop_without_thread_and_joined_thread() -> None:
    from openpine.data.periodic_fetcher import PeriodicBarFetcher, RefreshConfig

    fetcher = PeriodicBarFetcher(
        config=RefreshConfig(interval_seconds=0.01), registry=object(), orchestrator=object()
    )
    fetcher._running = True
    fetcher._thread = None
    fetcher.stop()
    assert fetcher._running is False

    class JoinedThread:
        alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            assert timeout == 0.25
            self.alive = False

    fetcher_with_thread = PeriodicBarFetcher(
        config=RefreshConfig(interval_seconds=0.01), registry=object(), orchestrator=object()
    )
    fetcher_with_thread._running = True
    fetcher_with_thread._thread = JoinedThread()  # type: ignore[assignment]
    fetcher_with_thread.stop(timeout=0.25)
    assert fetcher_with_thread._running is False


def test_provider_adapter_cache_miss_valid_coverage_and_footprint_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pinelib.core import bar as pinelib_bar

    from openpine.data import provider_adapter

    monkeypatch.setattr(
        pinelib_bar,
        "from_contract_bar",
        lambda bar: SimpleNamespace(time=bar.time, close=bar.close),
    )

    class Orchestrator:
        def load_bars(self, requested: BarQuery) -> SimpleNamespace:
            return SimpleNamespace(bars=(_bar(0), _bar(60_000), _bar(120_000)))

    adapter = object.__new__(provider_adapter.RuntimeDataProviderAdapter)
    adapter._orchestrator = Orchestrator()
    adapter.exchange = "binance"
    adapter.market = "spot"
    adapter.prefetch_end_ms = None
    adapter._bars_cache = {
        ("binance", "spot", "BTCUSDT", "1m"): (
            0,
            60_000,
            [SimpleNamespace(time=0, close=1.0)],
            [0],
        )
    }

    bars = adapter.get_bars("btcusdt", "1m", 0, 120_000)
    assert [bar.time for bar in bars] == [0, 60_000]

    coverage = provider_adapter._coverage_for(_query(), (_bar(0), _bar(60_000)), "unit")
    assert coverage.status == "valid"

    config = object()
    monkeypatch.setattr(provider_adapter, "ensure_marketdata_provider_version", lambda: None)
    monkeypatch.setattr(
        provider_adapter,
        "create_footprint_provider",
        lambda cfg: ("footprint-provider", cfg),
    )
    assert provider_adapter.create_local_footprint_provider_adapter(config=config) == (
        "footprint-provider",
        config,
    )


def test_candle_storage_detect_gaps_no_gap_condition_loops_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openpine.data.candle_storage import CandleStorage

    storage = CandleStorage(data_root=tmp_path / "data", sqlite_path=tmp_path / "db.sqlite")
    manifests = [
        SimpleNamespace(min_open_time=0, max_open_time=60_000),
        SimpleNamespace(min_open_time=120_000, max_open_time=120_000),
        SimpleNamespace(min_open_time=180_000, max_open_time=180_000),
    ]
    monkeypatch.setattr(storage, "list_manifests", lambda _query: manifests)
    assert storage.detect_gaps(_query(0, 240_000)) == []


def test_export_plot_record_fallthrough_and_equity_older_selected_loopback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openpine.export import plots
    from openpine.export.equity import initial_equity_at_export_start
    from openpine.export.window import ExportWindow

    monkeypatch.setattr(plots, "PLOT_COLUMNS", ["bar_time"])
    output = tmp_path / "plots.csv"
    rows = plots.export_plot_records([object(), (1_000, 9, 1.25, "value")], output)
    assert rows == 1
    assert "bar_index" not in pd.read_csv(output).columns

    equity = initial_equity_at_export_start(
        [
            {"bar_time_ms": 1_000, "equity": 10.0},
            {"bar_time_ms": 500, "equity": 5.0},
            {"bar_time_ms": 750, "equity": 7.5},
        ],
        ExportWindow(from_ms=1_000, to_ms=2_000),
    )
    assert equity == 10.0
