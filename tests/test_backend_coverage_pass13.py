from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from click.testing import CliRunner

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe
from openpine.batch import tv_corpus
from openpine.cli import compare as cmp
from openpine.data import provider_adapter as provider_adapter_mod
from openpine.streams import provider_adapter as stream_provider_adapter

from openpine.cli.data import (
    _parse_data_backfill_window,
    data as data_group,
)
from openpine.gateway.routes import accounts_data
from openpine.storage.backtest_dto import (
    BacktestArtifact,
    BacktestMetricsSummary,
    BacktestRun,
    BacktestTrade,
)


cli_main = importlib.import_module("openpine.cli.main")


@dataclass
class _Source:
    id: str = "pine1"
    name: str = "pine1"
    active_artifact_id: str | None = "art1"


class _FakePineRegistry:
    source = _Source()

    def get_source(self, name: str):
        if name == "missing":
            raise KeyError(name)
        return self.source

    def close(self):
        pass


class _Conn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.committed = False

    def execute(self, sql: str, params: tuple = ()):  # pragma: no cover - coverage checks caller branches
        self.executed.append((sql, params))
        return self

    def commit(self):
        self.committed = True


class _FakeStrategyRegistry:
    def __init__(self):
        self._mem: dict[str, object] = {}
        self._conn = _Conn()
        self.closed = False
        self.status_updates: list[tuple[str, str]] = []
        self.registered: list[dict] = []
        self._seed()

    def _seed(self):
        if self._mem:
            return
        self._mem["s1"] = SimpleNamespace(
            strategy_id="s1",
            name="Mean Reversion",
            pine_id="pine1",
            artifact_id="art1",
            params_json=json.dumps({"len": "20"}),
            params_hash="hash1",
            symbol="BTCUSDT",
            timeframe="1h",
            exchange="binance",
            market_type="spot",
            price_type="trade",
            mode="paper",
            enabled=True,
            status="paused",
            created_at=1_700_000_000_000,
            updated_at=1_700_000_100_000,
        )
        self._mem["err"] = SimpleNamespace(**{**self._mem["s1"].__dict__, "strategy_id": "err", "status": "error"})

    def list_strategies(self, status=None):
        values = list(self._mem.values())
        if status is not None:
            values = [item for item in values if item.status == status]
        return values

    def get_strategy(self, strategy_id: str):
        if strategy_id not in self._mem:
            raise KeyError(strategy_id)
        return self._mem[strategy_id]

    def register_strategy(self, **kwargs):
        self.registered.append(kwargs)
        strategy_id = kwargs.get("name") or "generated"
        item = SimpleNamespace(
            strategy_id=strategy_id,
            name=strategy_id,
            pine_id=kwargs.get("pine_id", "pine1"),
            artifact_id=kwargs["artifact_id"],
            params_json=json.dumps(kwargs.get("params") or {}, sort_keys=True),
            params_hash="newhash",
            symbol=kwargs["symbol"],
            timeframe=kwargs["timeframe"],
            exchange=kwargs.get("exchange", "binance"),
            market_type=kwargs.get("market_type", "spot"),
            price_type="trade",
            mode=kwargs.get("mode", "paper"),
            enabled=False,
            status="pending",
            created_at=1,
            updated_at=1,
        )
        self._mem[strategy_id] = item
        return item

    def update_status(self, strategy_id: str, status: str):
        self.status_updates.append((strategy_id, status))
        self.get_strategy(strategy_id).status = status

    def close(self):
        self.closed = True


class _FakeBacktestStore:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.run = BacktestRun(
            run_id="run1",
            strategy_id="s1",
            pine_id="pine1",
            artifact_id="art1",
            params_hash="hash1",
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            price_type="trade",
            timeframe="1h",
            from_time=1,
            to_time=3,
            warmup_bars=0,
            status="completed",
            started_at=1,
            finished_at=2,
            metrics=BacktestMetricsSummary(final_equity=1100, trades_total=1, win_rate=100),
        )
        self.equity = tmp_path / "equity.parquet"
        self.plots = tmp_path / "plots.parquet"
        self.equity.write_text("dummy-equity")
        self.plots.write_text("dummy-plots")

    def get_latest_run(self, strategy_id: str):
        return self.run if strategy_id == "s1" else None

    def get_run(self, run_id: str):
        return self.run if run_id == "run1" else None

    def list_runs(self, strategy_id: str, limit: int = 20):
        return [self.run] if strategy_id == "s1" else []

    def list_trades(self, run_id: str):
        return [
            BacktestTrade(
                trade_id="t1",
                run_id=run_id,
                strategy_id="s1",
                direction="long",
                entry_time=1,
                entry_price=100.0,
                qty=1.0,
                exit_time=2,
                exit_price=110.0,
                net_pnl=10.0,
                bars_held=1,
                exit_reason="tp",
            )
        ]

    def list_artifacts(self, run_id: str):
        return [
            BacktestArtifact("a1", run_id, "s1", "equity_curve", str(self.equity), "parquet", 2),
            BacktestArtifact("a2", run_id, "s1", "plot_outputs", str(self.plots), "parquet", 2),
        ]

    def close(self):
        pass


def _patch_strategy_dependencies(monkeypatch, tmp_path):
    registry = _FakeStrategyRegistry()
    monkeypatch.setattr("openpine.registry.SQLiteStrategyRegistry", lambda: registry)
    monkeypatch.setattr("openpine.pine.registry.SQLitePineSourceRegistry", lambda: _FakePineRegistry())
    store = _FakeBacktestStore(tmp_path)
    monkeypatch.setattr("openpine.storage.BacktestResultStore", lambda: store)
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.DataFrame({"time": [1, 2], "title": ["close", "close"], "equity": [1000, 1100], "value": [1.0, 2.0]}))
    monkeypatch.setattr(
        "openpine.config.OpenPineConfig.load",
        lambda: SimpleNamespace(live_enabled=True, data_dir=tmp_path, config_dir=tmp_path),
    )
    return registry, store


def test_top_level_run_dispatches_indicator_and_strategy(monkeypatch, tmp_path):
    commands: list[list[str]] = []
    monkeypatch.setattr(cli_main, "_run_openpine_cli", lambda args: commands.append(args) or ("Strategy created: s99\n" if args[:2] == ["strategy", "create"] else ""))

    indicator = tmp_path / "001_my_indicator.pine"
    indicator.write_text('//@version=6\nindicator("x")\nplot(close)')
    strategy = tmp_path / "my_strategy.pine"
    strategy.write_text('//@version=6\nstrategy("s")\n')
    runner = CliRunner()
    base = ["--symbol", "BTCUSDT", "--timeframe", "1h", "--from", "2026-01-01", "--output", str(tmp_path / "out")]

    result = runner.invoke(cli_main.cli, ["run", str(indicator), *base, "--to", "2026-01-02", "--tv-chart", str(tmp_path / "tv.csv")])
    assert result.exit_code == 0
    assert any(cmd[:2] == ["pine", "run-plots"] for cmd in commands)
    assert any(cmd[:2] == ["pine", "compare-tv"] for cmd in commands)

    commands.clear()
    result = runner.invoke(cli_main.cli, ["run", str(strategy), *base, "--capture-plots", "--compare-from", "2026-01-01"])
    assert result.exit_code == 0
    assert any(cmd[:2] == ["strategy", "create"] for cmd in commands)
    assert any(cmd[:2] == ["strategy", "backtest"] for cmd in commands)


def test_strategy_cli_lifecycle_and_result_commands(monkeypatch, tmp_path):
    registry, _store = _patch_strategy_dependencies(monkeypatch, tmp_path)
    runner = CliRunner()

    cases = [
        ["strategy", "list"],
        ["strategy", "list", "--json"],
        ["strategy", "show", "s1"],
        ["strategy", "status", "s1"],
        ["strategy", "create", "s2", "--pine", "pine1", "--symbol", "ETHUSDT", "--timeframe", "5m", "--mode", "live", "--param", "x=1"],
        ["strategy", "update", "s1", "--param", "len=21"],
        ["strategy", "pause", "s1"],
        ["strategy", "resume", "s1"],
        ["strategy", "paper", "s1", "start"],
        ["strategy", "paper", "s1", "stop"],
        ["strategy", "live", "s1", "enable"],
        ["strategy", "live", "s1", "start"],
        ["strategy", "live", "s1", "stop"],
        ["strategy", "error", "err", "clear", "--to", "disabled"],
        ["strategy", "metrics", "s1", "--json"],
        ["strategy", "runs", "s1", "--json"],
        ["strategy", "run", "run1", "--json"],
        ["strategy", "trades", "s1", "--json"],
        ["strategy", "equity", "s1", "--tail", "1"],
        ["strategy", "plots", "s1", "--latest"],
        ["strategy", "export-run", "s1", "--output", str(tmp_path / "export"), "--no-plots"],
    ]
    for args in cases:
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output, result.exception)

    assert ("s1", "running") in registry.status_updates
    assert (tmp_path / "export" / "trades.csv").exists()


def test_strategy_cli_failure_paths(monkeypatch, tmp_path):
    _patch_strategy_dependencies(monkeypatch, tmp_path)
    runner = CliRunner()
    for args, expected in [
        (["strategy", "show", "missing"], "Strategy not found"),
        (["strategy", "create", "bad", "--pine", "pine1", "--symbol", "X", "--timeframe", "1m", "--param", "broken"], "Invalid param"),
        (["strategy", "resume", "err"], "Cannot resume"),
        (["strategy", "paper", "err", "start"], "Cannot start paper"),
        (["strategy", "error", "s1", "clear"], "not in error state"),
        (["strategy", "compare-tv", "s1", "--output", str(tmp_path / "cmp")], "Pass at least one TV file"),
    ]:
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code != 0
        assert expected in result.output


def test_data_cli_pure_helpers_and_providers(monkeypatch, tmp_path):
    assert _parse_data_backfill_window(from_date="bad", to_date=None, now_ms=10)[2]
    start, end, error = _parse_data_backfill_window(from_date="2026-01-01", to_date="2026-01-02", now_ms=10)
    assert error is None and start is not None and end is not None and start < end
    assert accounts_data._estimate_bars_for_window(0, 60_000, "1m") == 1
    assert accounts_data._ranges_cover_request([{"from_ms": 0, "to_ms": 60_000}], "1m", 0, 120_000)
    assert not accounts_data._ranges_cover_request([], "1m", 0, 60_000)

    monkeypatch.setattr("openpine.data.provider_adapter.create_local_marketdata_provider_adapter", lambda: object())
    result = CliRunner().invoke(data_group, ["providers"])
    assert result.exit_code == 0
    assert "Binance" in result.output
    assert "Local" in result.output


def test_accounts_data_inventory_helpers(tmp_path):
    ranges = [
        {"from_ms": 0, "to_ms": 0, "rows": 1, "sources": {"a"}},
        {"from_ms": 60_000, "to_ms": 120_000, "rows": 2, "sources": {"b"}},
        {"rows": 3, "sources": {"fallback"}},
    ]
    compact = accounts_data._compact_ranges(accounts_data._coalesce_ranges(ranges, "1m"))
    assert compact[0]["source"]
    assert accounts_data._estimate_unique_bars(ranges, "1m") >= 6
    assert accounts_data._freshness_status(None, "1m") == "empty"
    assert accounts_data._series_role({"timeframe": "1m"}) == "source"
    assert accounts_data._series_role({"timeframe": "5m", "source_kinds": ["aggregate"]}) == "derived"
    group_key = ("binance", "spot", "BTCUSDT", "trade", "1m")
    entry = accounts_data._series_entry({}, group_key)
    accounts_data._extend_series(entry, 1, 0, 0, 7, "unit", "source1")
    assert entry["bar_count"] == 1
    assert accounts_data._series_id(group_key)
    assert accounts_data._marketdata_segment_dir(tmp_path, group_key[0], group_key[1], group_key[2], group_key[4], "trade_kline").as_posix().endswith("timeframe=1m")
    file_path = tmp_path / "x" / "a.txt"
    file_path.parent.mkdir()
    file_path.write_text("abc")
    assert accounts_data._dir_size(tmp_path) >= 3



def _csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_compare_helpers_normalize_trades_and_reports(tmp_path):
    assert cmp._compare_csv_float("1,25") == 1.25
    assert cmp._compare_csv_float("1,250.5") == 1250.5
    assert cmp._compare_csv_float("na") != cmp._compare_csv_float("na")
    assert cmp._compare_csv_time_ms("1700000000") == 1_700_000_000_000
    assert cmp._compare_csv_time_ms("bad") is None
    assert cmp._compare_normalized_header(" Net\xa0Profit ") == "net profit"
    assert cmp._find_compare_column(["Net Profit %", "Net Profit"], "net profit", reject=("%",)) == "Net Profit"
    assert cmp._trade_action_and_direction("Entry Long") == ("entry", "long")
    assert cmp._trade_action_and_direction("Выход корот") == ("exit", "short")

    tv_trades = _csv(
        tmp_path / "tv_trades.csv",
        "Trade #,Type,Date/Time,Signal,Price,Qty,Net Profit,Run-up,Drawdown\n"
        "1,Entry Long,2026-01-01T00:00:00Z,L,100,1,,,\n"
        "1,Exit Long,2026-01-01T01:00:00Z,X,110,1,10,12,-1\n"
        "2,Entry Short,2026-01-01T02:00:00Z,S,120,2,,,\n",
    )
    normalized = cmp._write_normalized_tv_trades(
        tv_path=tv_trades,
        output_path=tmp_path / "normalized.csv",
        compare_from_ms=None,
        compare_to_ms=None,
    )
    fields, rows = cmp._read_compare_csv(normalized)
    assert "entry_time_ms" in fields
    assert len(rows) == 2
    assert rows[0]["status"] == "closed"

    bad_tv = _csv(tmp_path / "bad_tv.csv", "Type,Date/Time,Price\nEntry Long,now,1\n")
    try:
        cmp._write_normalized_tv_trades(tv_path=bad_tv, output_path=tmp_path / "bad.csv", compare_from_ms=None, compare_to_ms=None)
    except Exception as exc:
        assert "missing columns" in str(exc)


def test_compare_rows_and_strategy_report(tmp_path):
    tv = _csv(tmp_path / "tv.csv", "time,plot,extra\n1000,1.0,x\n2000,2.0,y\n")
    op = _csv(tmp_path / "op.csv", "bar_time,plot\n1000,1.0\n2000,2.2\n3000,3.0\n")
    summary, top = cmp._compare_rows_by_time(
        tv_path=tv,
        op_path=op,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.01,
        rel_tol=0.0,
    )
    assert summary["status"] == "mismatch"
    assert top[0]["column"] == "plot"

    tv_order = _csv(tmp_path / "tv_order.csv", "a,b\n1,hello\n2,world\n")
    op_order = _csv(tmp_path / "op_order.csv", "a,b\n1,hello\n3,world!\n4,extra\n")
    order_summary, order_top = cmp._compare_rows_by_order(
        tv_path=tv_order,
        op_path=op_order,
        exclude_columns=set(),
        abs_tol=0.01,
        rel_tol=0.0,
    )
    assert "row_count_mismatch" in order_summary["classification"]
    assert order_top

    run = SimpleNamespace(run_id="run1")
    result = cmp._compare_strategy_run_with_tv_exports(
        strategy_id="s1",
        run=run,
        exported={"plots": str(op), "equity": str(op_order), "trades": str(op_order)},
        output_path=tmp_path / "report",
        tv_chart=str(tv),
        tv_trades=None,
        tv_equity=None,
        abs_tol=0.01,
        rel_tol=0.0,
        include_base_columns=True,
        compare_from_ms=None,
        compare_to_ms=None,
    )
    assert result["comparisons"]
    assert (tmp_path / "report" / "comparison_summary.json").exists()



def test_tv_corpus_manifest_visible_bars_and_filters(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    export_root = root / "exports" / "case1"
    export_root.mkdir(parents=True)
    (export_root / "source.pine").write_text('//@version=6\nindicator("x")')
    chart = export_root / "BTCUSDT, 15.csv"
    chart.write_text(
        "time,open,high,low,close,Volume\n"
        "1700000000,1,2,0.5,1.5,10\n"
        "1700000900,1.5,2.5,1,2,11\n",
        encoding="utf-8",
    )
    manifest = root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "7,case1,indicator,grp,source.pine,\"BTCUSDT, 15.csv\",15m\n",
        encoding="utf-8",
    )

    assert tv_corpus.sanitize_name("  bad/name  ") == "bad_name"
    assert tv_corpus.timeframe_from_name(chart) == "15m"
    df = tv_corpus.read_chart(chart)
    assert "bar_time" in df.columns
    assert tv_corpus.infer_timeframe(chart, df) == "15m"
    export = tv_corpus.build_chart_export(chart)
    assert export.bars == 2
    entries = tv_corpus.load_manifest(manifest, root)
    assert tv_corpus.openpine_name(entries[0]).startswith("po_0007")
    assert tv_corpus.strategy_name(entries[0], "15m")
    assert tv_corpus.filter_entries(entries, kind="indicator", timeframe="15", limit=1, start_id=1, only_id={7}) == entries
    assert tv_corpus.filter_entries(entries, kind="strategy", timeframe=None, limit=None, start_id=None, only_id=None) == []

    bars, meta = tv_corpus.load_visible_bars_by_time(root=root, manifest=manifest, source_group="grp", timeframe="15m", symbol="BTCUSDT")
    assert meta["unique_bars"] == 2
    assert len(bars) == 2

    provider_bars = [SimpleNamespace(time=1700000000000, open=0, high=0, low=0, close=0, volume=0)]
    # SimpleNamespace is not replaceable, so use a dataclass for the merge path.
    @dataclass(frozen=True)
    class BarLike:
        time: int
        open: float
        high: float
        low: float
        close: float
        volume: float

    patched, count = tv_corpus.merge_visible_bars(
        provider_bars=[BarLike(1700000000000, 0, 0, 0, 0, 0), BarLike(42, 1, 1, 1, 1, 1)],
        visible_bars_by_time=bars,
    )
    assert count == 1
    assert patched[0].close == 1.5
    assert tv_corpus.merge_visible_bars(provider_bars=provider_bars, visible_bars_by_time={})[1] == 0


def test_tv_corpus_error_paths(tmp_path):
    bad_chart = tmp_path / "bad.csv"
    bad_chart.write_text("time,open\n1,1\n", encoding="utf-8")
    try:
        tv_corpus.read_chart(bad_chart)
    except ValueError as exc:
        assert "missing OHLC" in str(exc)
    empty_chart = tmp_path / "empty.csv"
    empty_chart.write_text("time,open,high,low,close\n", encoding="utf-8")
    try:
        tv_corpus.read_chart(empty_chart)
    except ValueError as exc:
        assert "empty" in str(exc)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n1,missing,indicator,grp,,x.csv,15m\n")
    try:
        tv_corpus.load_manifest(manifest, tmp_path)
    except ValueError as exc:
        assert "no pine_files" in str(exc)


def test_data_cli_backfill_repair_gaps_inspect_doctor(monkeypatch, tmp_path):
    runner = CliRunner()

    wait_calls = []
    monkeypatch.setattr("openpine.cli.data._run_sync_marketdata_backfill", lambda **kwargs: wait_calls.append(kwargs) or True)
    bad = runner.invoke(data_group, ["backfill", "BTCUSDT", "1m", "--from", "bad"])
    assert bad.exit_code == 0 and "Invalid --from" in bad.output
    queued = runner.invoke(data_group, ["backfill", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"])
    assert queued.exit_code == 0 and "Backfill job enqueued" in queued.output
    waited = runner.invoke(data_group, ["backfill", "BTCUSDT", "1m", "--from", "2026-01-01", "--wait", "--timeout", "1"])
    assert waited.exit_code == 0 and wait_calls

    class FakeRegistry:
        def list_strategies(self):
            return [SimpleNamespace(strategy_id="s1", symbol="BTCUSDT", timeframe="1m", exchange="binance", status="running")]
        def update_status(self, sid, status):
            self.updated = (sid, status)
        def close(self):
            pass
    monkeypatch.setattr("openpine.registry.SQLiteStrategyRegistry", FakeRegistry)
    invalid = runner.invoke(data_group, ["repair", "BTCUSDT", "1m", "--from", "10", "--to", "5"])
    assert invalid.exit_code == 1
    repaired = runner.invoke(data_group, ["repair", "BTCUSDT", "1m", "--from", "0", "--to", "60000"])
    assert repaired.exit_code == 0 and "Affected strategies" in repaired.output

    class FakeOrchestrator:
        def detect_gaps(self, query):
            return [SimpleNamespace(gap_start=0, gap_end=120_000)]
        def load_bars(self, query):
            bars = [SimpleNamespace(time=0, open=1, high=2, low=0.5, close=1.5, volume=10)]
            coverage = SimpleNamespace(status="valid", missing_intervals=(), duplicate_timestamps=())
            return SimpleNamespace(bars=bars, coverage=coverage)
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", FakeOrchestrator)
    gaps = runner.invoke(data_group, ["gaps", "BTCUSDT", "1m"])
    assert gaps.exit_code == 0 and "gap(s) found" in gaps.output
    inspected = runner.invoke(data_group, ["inspect", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"])
    assert inspected.exit_code == 0 and "Canonical bars" in inspected.output
    doctor = runner.invoke(data_group, ["doctor", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"])
    assert doctor.exit_code == 0 and "DATA_OK" in doctor.output


def test_data_parallel_backfill_branches(monkeypatch):
    class FakeFetchJob:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class FakeFetcher:
        def __init__(self, max_workers=None):
            self.max_workers = max_workers
        def fetch_chunked(self, symbol, timeframe, start_ms, end_ms, exchange="binance"):
            return [1, 2, 3]
        def fetch_many(self, jobs, progress_callback=None):
            if progress_callback:
                progress_callback("BTCUSDT", 1, len(jobs))
            return {job.symbol: [1] for job in jobs}
    import openpine.data.parallel_fetcher as pf
    monkeypatch.setattr(pf, "ParallelDataFetcher", FakeFetcher)
    monkeypatch.setattr(pf, "FetchJob", FakeFetchJob)
    runner = CliRunner()
    bad = runner.invoke(data_group, ["parallel-backfill", "BTCUSDT", "1m", "--from", "bad"])
    assert bad.exit_code == 0 and "Invalid --from" in bad.output
    chunked = runner.invoke(data_group, ["parallel-backfill", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02", "--chunked"])
    assert chunked.exit_code == 0 and "Chunked fetch complete" in chunked.output
    many = runner.invoke(data_group, ["parallel-backfill", "BTCUSDT,ETHUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02", "--workers", "2"])
    assert many.exit_code == 0 and "Parallel backfill complete" in many.output



def _bar(time=0, close=1.0, *, instrument=None, timeframe=None):
    instrument = instrument or InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = timeframe or parse_timeframe("1m")
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        time=time,
        time_close=time + (timeframe.duration_ms or 0),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=10,
        closed=True,
    )


def test_provider_adapter_runtime_cache_and_helpers(monkeypatch, tmp_path):
    bars = [_bar(0, 1.0), _bar(60_000, 2.0), _bar(120_000, 3.0)]

    class FakeOrchestrator:
        calls = []
        def __init__(self, provider):
            self.provider = provider
        def load_bars(self, query):
            FakeOrchestrator.calls.append(query)
            return SimpleNamespace(bars=bars)

    import pinelib.core.bar as pinelib_bar
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(pinelib_bar, "from_contract_bar", lambda bar: SimpleNamespace(time=bar.time, close=bar.close))
    adapter = provider_adapter_mod.RuntimeDataProviderAdapter(object(), exchange="BINANCE", market="SPOT", prefetch_end_ms=180_000)
    result = adapter.get_bars("btcusdt", "1m", 0, 120_000, max_bars=1)
    assert [bar.close for bar in result] == [1.0]
    cached = adapter.get_bars("BTCUSDT", "1m", 60_000, 120_000)
    assert [bar.close for bar in cached] == [2.0]
    assert len(FakeOrchestrator.calls) == 1
    chart_bar = SimpleNamespace(time=0, time_close=60_000)
    assert adapter.get_intrabar_bars("BTCUSDT", chart_bar, "1m")
    try:
        adapter.get_bars("BTCUSDT", "1m", None, 60_000)
    except ValueError as exc:
        assert "bounded" in str(exc)

    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=0,
        end_ms=60_000,
    )
    normalized = provider_adapter_mod.normalize_provider_bar(
        {"timestamp": 0, "open": 1, "high": 2, "low": 0.5, "close": 1.5}, query
    )
    assert normalized.time_close == 60_000 and normalized.volume is None and normalized.closed is True
    normalized2 = provider_adapter_mod.normalize_provider_bar(
        {"open_time_ms": 0, "close_time_ms": 10, "exchange": "Bybit", "market": "Linear", "exchange_symbol": "ethusdt", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 5, "is_closed": False}, query
    )
    assert normalized2.instrument.exchange == "bybit" and normalized2.closed is False
    assert provider_adapter_mod._coverage_for(query, tuple(), "unit").status == "empty"
    duplicate = provider_adapter_mod._coverage_for(query, (bars[0], bars[0]), "unit")
    assert duplicate.status == "duplicate"
    unordered = provider_adapter_mod._coverage_for(query, (bars[1], bars[0]), "unit")
    assert unordered.status == "unordered"

    monkeypatch.setattr(provider_adapter_mod, "ensure_marketdata_provider_version", lambda: None)
    monkeypatch.setattr(provider_adapter_mod, "create_provider", lambda cfg: ("provider", cfg.storage.cache_dir))
    provider = provider_adapter_mod.create_local_marketdata_provider_adapter(cache_dir=tmp_path / "cache")
    assert provider[0] == "provider"
    monkeypatch.setattr(provider_adapter_mod, "create_footprint_provider", lambda cfg: ("footprint", cfg.storage.cache_dir))
    footprint = provider_adapter_mod.create_local_footprint_provider_adapter(cache_dir=tmp_path / "foot")
    assert footprint[0] == "footprint"


def test_exchange_metadata_cache_and_fallbacks(monkeypatch, tmp_path):
    import openpine.exchange_metadata as em
    em._BINANCE_SPOT_INFO = None
    assert em.default_qty_step("coinbase", "spot", "BTCUSDT") is None
    assert em.default_qty_rounding_mode("coinbase", "spot", "BTCUSDT") == "none"
    cache = tmp_path / "exchange.json"
    payload = {"symbols": [{"symbol": "ABCUSDT", "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.125"}]}]}
    cache.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("OPENPINE_BINANCE_EXCHANGE_INFO_CACHE", str(cache))
    assert em.default_qty_step("binance", "spot", "ABCUSDT") == 0.125
    assert em.default_qty_rounding_mode("binance", "spot", "ABCUSDT") == "truncate"
    assert em._filter({"filters": []}, "LOT_SIZE") is None
    assert em._float_or_none("bad") is None
    stale = tmp_path / "stale.json"
    stale.write_text("{bad", encoding="utf-8")
    assert em._read_cache(stale) is None
    unwritable = tmp_path / "missing" / "payload.json"
    em._write_cache(unwritable, payload)
    assert unwritable.exists()


def test_stream_provider_adapter_normalization_and_subscription(monkeypatch):
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    nested = SimpleNamespace(bar={"open_time": 0, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 7, "is_closed": True})
    envelope = stream_provider_adapter.normalize_provider_kline_update(nested, instrument_key=instrument, timeframe=timeframe)
    assert envelope.closed is True and envelope.volume == 7
    direct = stream_provider_adapter.normalize_provider_kline_update(
        SimpleNamespace(instrument={"exchange": "binance", "market": "spot", "symbol": "ETHUSDT"}, timeframe={"raw": "1m", "canonical": "1m", "multiplier": 1, "unit": "minute", "duration_ms": 60000}, time=0, open=1, high=2, low=0, close=1),
        instrument_key=instrument,
        timeframe=timeframe,
    )
    bar = stream_provider_adapter.envelope_to_bar(direct)
    assert bar.instrument.symbol == "ETHUSDT"

    async def _events():
        yield SimpleNamespace(update={"open_time": 0, "open": 1, "high": 2, "low": 0, "close": 1, "is_closed": False})
        yield SimpleNamespace(update={"open_time": 60_000, "open": 2, "high": 3, "low": 1, "close": 2, "is_closed": True})

    class FakeClient:
        def events(self):
            return _events()

    monkeypatch.setattr(stream_provider_adapter, "ensure_marketdata_provider_version", lambda: None)
    monkeypatch.setattr(stream_provider_adapter, "create_live_kline_client", lambda *args, **kwargs: FakeClient())

    async def run_flow():
        adapter = stream_provider_adapter.create_local_live_data_feed_adapter(["ignored"])
        seen = []
        adapter.on_bar(seen.append)
        await adapter.connect()
        await adapter.subscribe(instrument, timeframe)
        await asyncio.sleep(0)
        await adapter.disconnect()
        assert [bar.close for bar in seen] == [2]
        await adapter.unsubscribe(instrument, timeframe)

    import asyncio
    asyncio.run(run_flow())
