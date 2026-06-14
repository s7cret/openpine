from __future__ import annotations

import asyncio
import json

import pytest


def _query(start: int = 0, end: int = 120_000, market: str = "spot"):
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    return BarQuery(
        instrument=InstrumentKey(exchange="binance", market=market, symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=start,
        end_ms=end,
        source="provider",
    )


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_direct_provider_success_cache_and_error_paths(monkeypatch, tmp_path):
    from openpine.data import direct_provider as module
    from openpine.data.direct_provider import DirectBinanceProvider

    module._EARLIEST_OPEN_CACHE.clear()
    monkeypatch.setenv("OPENPINE_DATA_CACHE", "0")

    calls: list[str] = []

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append(url)
        if "startTime=0" in url:
            return _FakeResponse([[60_000]])
        return _FakeResponse(
            [
                [60_000, "1", "2", "0.5", "1.5", "10"],
                [120_000, "2", "3", "1.5", "2.5", "11"],
            ]
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    provider = DirectBinanceProvider()
    progress = []
    series = provider.fetch_bars(_query(start=0, end=180_000), lambda *args: progress.append(args))

    assert provider.get_earliest_open_time(_query()) == 60_000
    assert [bar.time for bar in series.bars] == [60_000, 120_000]
    assert series.coverage.status == "valid"
    assert progress[0][-1] == "cache_lookup"
    assert progress[-1][-1] == "fetch_done"
    assert calls[0].startswith("https://api.binance.com")

    futures = DirectBinanceProvider._binance_context(_query(market="futures"))
    assert futures[2].startswith("https://fapi.binance.com")

    module._EARLIEST_OPEN_CACHE.clear()

    def failing_urlopen(req, timeout=0):
        raise OSError("offline")

    monkeypatch.setattr(module.urllib.request, "urlopen", failing_urlopen)
    assert provider.get_earliest_open_time(_query()) is None
    empty = provider.fetch_bars(_query())
    assert empty.coverage.status == "empty"


def test_direct_provider_uses_persistent_cache(monkeypatch, tmp_path):
    from openpine.data import direct_provider as module
    from openpine.data.direct_provider import DirectBinanceProvider
    from openpine.data.persistent_cache import save_bar_series
    from marketdata_provider.contracts import Bar, BarSeries, CoverageReport

    query = _query(start=60_000, end=120_000)
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("OPENPINE_DATA_CACHE", "1")
    monkeypatch.setenv("OPENPINE_DATA_CACHE_DIR", str(cache_dir))
    module._EARLIEST_OPEN_CACHE.clear()
    monkeypatch.setattr(DirectBinanceProvider, "get_earliest_open_time", lambda self, q: 60_000)
    save_bar_series(
        cache_dir,
        BarSeries(
            query=query,
            bars=(
                Bar(
                    instrument=query.instrument,
                    timeframe=query.timeframe,
                    time=60_000,
                    time_close=120_000,
                    open=1,
                    high=2,
                    low=0.5,
                    close=1.5,
                    volume=10,
                    closed=True,
                ),
            ),
            coverage=CoverageReport(
                requested_start_ms=query.start_ms,
                requested_end_ms=query.end_ms,
                delivered_start_ms=60_000,
                delivered_end_ms=120_000,
                missing_intervals=(),
                duplicate_timestamps=(),
                source_mix=("test",),
                status="valid",
            ),
        ),
    )

    fetched = DirectBinanceProvider().fetch_bars(query)
    assert len(fetched.bars) == 1
    assert fetched.coverage.source_mix == ("persistent_cache",)


def test_snapshot_policy_domain_aliases_and_structlog(capsys):
    from openpine._compat import structlog
    from openpine.domain import Bar, InstrumentKey, Timeframe
    from openpine.state.policy import SnapshotPolicy
    from openpine.state.store import SavePolicy

    assert InstrumentKey is not None and Timeframe is not None and Bar is not None
    assert SnapshotPolicy().should_save(0) is True
    assert SnapshotPolicy().should_save(0, failed_bar=True) is False
    assert SnapshotPolicy(SavePolicy.INTERVAL, 3).should_save(2) is False
    assert SnapshotPolicy(SavePolicy.INTERVAL, 3).should_save(3) is True
    assert SnapshotPolicy(SavePolicy.ON_REQUEST).should_save(999) is False
    logger = structlog.get_logger("coverage")
    logger.info("hello", answer=42)
    out = capsys.readouterr().out
    assert "hello" in out or out == ""


@pytest.mark.asyncio
async def test_telegram_daemon_service_lifecycle(monkeypatch):
    from openpine.config.model import OpenPineConfig
    from openpine.daemon.telegram_service import TelegramDaemonService

    events: list[str] = []

    class FakePlugin:
        def __init__(self, config):
            self.config = config

    class FakeHandler:
        def __init__(self, plugin, commands_module, cli_path):
            self.plugin = plugin
            self.commands_module = commands_module
            self.cli_path = cli_path
            events.append("handler")

        async def run(self, poll_interval=0):
            events.append(f"run:{poll_interval}")
            await asyncio.Event().wait()

        async def stop(self):
            events.append("stop")

    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: OpenPineConfig()))
    monkeypatch.setattr("openpine.daemon.telegram_service.TelegramCommandPlugin", FakePlugin)
    monkeypatch.setattr("openpine.daemon.telegram_service.TelegramBotHandler", FakeHandler)
    svc = TelegramDaemonService(cli_path="custom-openpine")
    await svc._on_start()
    assert svc._handler is not None
    assert svc._handler.commands_module is not None
    await asyncio.sleep(0)
    await svc._on_stop(timeout=0.1)
    assert events[0] == "handler"
    assert "run:10.0" in events
    assert "stop" in events


@pytest.mark.asyncio
async def test_telegram_poll_loop_without_handler_and_error(monkeypatch):
    from openpine.daemon.telegram_service import TelegramDaemonService

    svc = TelegramDaemonService()
    await svc._run_poll_loop()

    class BadHandler:
        async def run(self, poll_interval=0):
            raise RuntimeError("boom")

        async def stop(self):
            pass

    svc._handler = BadHandler()
    with pytest.raises(RuntimeError, match="boom"):
        await svc._run_poll_loop()

    class SlowTask:
        def cancel(self):
            pass

    svc._poll_task = asyncio.create_task(asyncio.sleep(10))
    svc._poll_task.cancel()
    await svc._on_stop(timeout=0.1)
