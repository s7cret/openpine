from __future__ import annotations

import json
import urllib.request

import pytest
from click.testing import CliRunner

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, InstrumentKey, parse_timeframe

from openpine.cli.data import data as data_group
from openpine.data.periodic_fetcher import PeriodicBarFetcher, RawMarketKey, RefreshConfig, _group_strategies_by_market
from openpine.data.orchestrator import DataOrchestrator, StorageUnavailableError
from openpine.registry.strategies import StrategyInstance


class _Console:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *parts, **kwargs) -> None:
        self.messages.append(" ".join(str(p) for p in parts))


def _bar(open_time: int, tf: str = "1m", close: float = 1.0) -> Bar:
    timeframe = parse_timeframe(tf)
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=timeframe,
        time=open_time,
        time_close=open_time + (timeframe.duration_ms or 60_000),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=10,
        closed=True,
    )


def _series(bars: list[Bar]) -> BarSeries:
    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=0,
        end_ms=120_000,
        source="storage",
    )
    return BarSeries(query=query, bars=tuple(bars), coverage=DataOrchestrator.coverage_for_series(query, tuple(bars), "test"))


def _strategy(strategy_id: str = "s1", *, enabled: bool = True, timeframe: str = "1m", status: str = "running") -> StrategyInstance:
    return StrategyInstance(
        strategy_id=strategy_id,
        name=strategy_id,
        pine_id="pine",
        artifact_id="artifact",
        params_json="{}",
        params_hash="ph",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe=timeframe,
        status=status,
        enabled=enabled,
        created_at=1,
        updated_at=2,
    )


def test_periodic_fetcher_lifecycle_variable_tf_conflicts_and_http(monkeypatch):
    class Registry:
        def __init__(self, strategies):
            self._strategies = strategies

        def list_strategies(self):
            return self._strategies

    class Orchestrator:
        def __init__(self):
            self.stored: list[BarSeries] = []
            self.raise_conflict = False
            self.latest = None

        def store_bars(self, series):
            if self.raise_conflict:
                raise StorageUnavailableError("conflicting closed candle")
            self.stored.append(series)

        def load_bars(self, query):
            return _series([_bar(0), _bar(60_000)])

        def latest_bar_time(self, query):
            return self.latest

    orch = Orchestrator()
    fetcher = PeriodicBarFetcher(RefreshConfig(interval_seconds=0.01, lookback_bars=2), Registry([_strategy("s1", timeframe="5m"), _strategy("s2", enabled=False)]), orch)
    assert _group_strategies_by_market([_strategy("s1"), _strategy("s2")])
    fetcher.start(); fetcher.start(); fetcher.stop(timeout=0.01); fetcher.stop()

    key = RawMarketKey.from_strategy(_strategy(timeframe="5m"))
    original_fetch_bars_direct = PeriodicBarFetcher._fetch_bars_direct
    monkeypatch.setattr(PeriodicBarFetcher, "_fetch_bars_direct", staticmethod(lambda *a, **k: [_bar(0), _bar(60_000), _bar(120_000), _bar(180_000), _bar(240_000)]))
    fetcher._refresh_market_key(key, [_strategy(timeframe="5m")], now_ms=360_000)
    assert orch.stored
    orch.raise_conflict = True
    fetcher._refresh_market_key(key, [_strategy(timeframe="5m")], now_ms=420_000)
    orch.raise_conflict = False
    orch.latest = 420_000
    fetcher._refresh_market_key(key, [_strategy(timeframe="5m")], now_ms=420_000)

    with pytest.raises(ValueError):
        PeriodicBarFetcher(RefreshConfig(source_timeframe="1M"), Registry([_strategy()]), orch)._refresh_market_key(key, [_strategy()], now_ms=1_000_000)

    monkeypatch.setattr(PeriodicBarFetcher, "_fetch_bars_direct", original_fetch_bars_direct)

    class Response:
        def __enter__(self): return self
        def __exit__(self, *exc): return None
        def read(self): return json.dumps([[0, "1", "2", "0.5", "1.5", "10"]]).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: Response())
    bars = PeriodicBarFetcher._fetch_bars_direct(RawMarketKey("binance", "futures", "btcusdt", "trade"), parse_timeframe("1m"), 0, 60_000)
    assert len(bars) == 1 and bars[0].instrument.market == "futures"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: (_ for _ in ()).throw(OSError("offline")))
    assert PeriodicBarFetcher._fetch_bars_direct(key, parse_timeframe("1m"), 0, 60_000) == []


def test_cli_data_commands_cover_success_and_error_paths(monkeypatch, tmp_path):
    runner = CliRunner()

    class FakeCursor:
        def __init__(self, rows): self._rows = rows
        def fetchall(self): return self._rows

    class FakeStorage:
        def __init__(self, path): self.path = path; self.closed = False
        def execute(self, sql, params=()):
            if "data_requirements" in sql:
                return FakeCursor([("binance", "BTCUSDT", "1m", "local", "ok", 60_000)])
            return FakeCursor([])
        def close(self): self.closed = True

    class FakeConfig:
        sqlite_path = tmp_path / "openpine.sqlite"
        data_dir = tmp_path / "data"

    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: FakeConfig())
    monkeypatch.setattr("openpine.storage.SQLiteStorage", FakeStorage)
    result = runner.invoke(data_group, ["status", "BTCUSDT", "--timeframe", "1m"])
    assert result.exit_code == 0 and "Data pipeline status" in result.output

    class FakeGap:
        gap_start = 0; gap_end = 3_600_000

    class FakeOrchestrator:
        def __init__(self, gaps=None, bars=None, fail=None):
            self.gaps = gaps if gaps is not None else []
            self.bars = bars if bars is not None else []
            self.fail = fail
        def detect_gaps(self, query):
            if self.fail: raise self.fail
            return self.gaps
        def load_bars(self, query):
            if self.fail: raise self.fail
            return _series(self.bars)

    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", lambda: FakeOrchestrator([FakeGap()]))
    assert runner.invoke(data_group, ["gaps", "BTCUSDT", "1m"]).exit_code == 0
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", lambda: FakeOrchestrator(fail=StorageUnavailableError("no db")))
    assert runner.invoke(data_group, ["gaps", "BTCUSDT", "1m"]).exit_code != 0

    class Scheduler:
        def enqueue(self, job): job.id = "abcdef123456"; return job
    monkeypatch.setattr("openpine.cli.data._cli_scheduler", Scheduler())

    class Registry:
        def list_strategies(self): return [_strategy("match"), _strategy("other", timeframe="5m")]
        def update_status(self, strategy_id, status): self.updated = (strategy_id, status)
        def close(self): pass
    monkeypatch.setattr("openpine.registry.SQLiteStrategyRegistry", Registry)
    ok = runner.invoke(data_group, ["repair", "BTCUSDT", "1m", "--from", "0", "--to", "60000"])
    assert ok.exit_code == 0 and "match" in ok.output
    bad = runner.invoke(data_group, ["repair", "BTCUSDT", "1m", "--from", "9", "--to", "1"])
    assert bad.exit_code != 0 or "Invalid repair window" in bad.output

    queued = runner.invoke(data_group, ["backfill", "BTCUSDT", "1m", "--from", "2024-01-01", "--to", "2024-01-02"])
    assert queued.exit_code == 0 and "Backfill job enqueued" in queued.output
    invalid = runner.invoke(data_group, ["backfill", "BTCUSDT", "1m", "--from", "bad"])
    assert invalid.exit_code == 0 and "Invalid --from" in invalid.output
    monkeypatch.setattr("openpine.cli.data._run_sync_marketdata_backfill", lambda **kw: True)
    waited = runner.invoke(data_group, ["backfill", "BTCUSDT", "1m", "--from", "2024-01-01", "--wait"])
    assert waited.exit_code == 0

    class Fetcher:
        def __init__(self, max_workers=None): self.max_workers = max_workers
        def fetch_chunked(self, symbol, timeframe, start_ms, end_ms, exchange): return [_bar(0)]
        def fetch_many(self, jobs, progress_callback=None):
            for idx, job in enumerate(jobs, start=1):
                if progress_callback: progress_callback(job.symbol, idx, len(jobs))
            return {job.symbol: [_bar(0)] for job in jobs}
    monkeypatch.setattr("openpine.data.parallel_fetcher.ParallelDataFetcher", Fetcher)
    assert runner.invoke(data_group, ["parallel-backfill", "BTCUSDT", "1m", "--from", "2024-01-01", "--chunked"]).exit_code == 0
    assert runner.invoke(data_group, ["parallel-backfill", "BTCUSDT,ETHUSDT", "1m", "--from", "2024-01-01"]).exit_code == 0
    assert "Invalid --from" in runner.invoke(data_group, ["parallel-backfill", "BTCUSDT", "1m", "--from", "bad"]).output

    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", lambda: FakeOrchestrator(bars=[_bar(0)]))
    inspect_ok = runner.invoke(data_group, ["inspect", "BTCUSDT", "1m", "--from", "2024-01-01", "--to", "2024-01-02"])
    assert inspect_ok.exit_code == 0 and "Canonical bars" in inspect_ok.output
    doctor_ok = runner.invoke(data_group, ["doctor", "BTCUSDT", "1m", "--from", "2024-01-01", "--to", "2024-01-02"])
    assert doctor_ok.exit_code == 0 and "Diagnostic Report" in doctor_ok.output
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", lambda: FakeOrchestrator(fail=Exception("x")))
    # inspect only catches DataCoverageError; keep invalid date branch instead.
    assert "Invalid --from" in runner.invoke(data_group, ["inspect", "BTCUSDT", "1m", "--from", "bad"]).output
    assert "Invalid --from" in runner.invoke(data_group, ["doctor", "BTCUSDT", "1m", "--from", "bad"]).output

    monkeypatch.setattr("openpine.data.provider_adapter.create_local_marketdata_provider_adapter", lambda: object())
    providers = runner.invoke(data_group, ["providers"])
    assert providers.exit_code == 0 and "Available Data Providers" in providers.output
