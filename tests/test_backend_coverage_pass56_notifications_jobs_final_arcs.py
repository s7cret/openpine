from __future__ import annotations

import importlib.util
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.daemon.telegram_service import TelegramDaemonService
from openpine.gateway.ws_manager import ConnectionManager
from openpine.jobs.models import Job, JobStatus, JobType
from openpine.jobs.scheduler import JobScheduler
from openpine.notifications import telegram as tg
from openpine.registry.strategies import StrategyInstance
from openpine.storage.strategy_ledger import LedgerSource, PositionSide
from openpine.workers.pool import WorkerPool
from openpine.workers.strategy_fanout import (
    FanoutStatus,
    StrategyBarFanout,
    StrategyBarFanoutConfig,
)
from openpine.workers.strategy_job_executor import StrategyJobExecutor


class _TelegramPlugin:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any] | None]] = []
        self.answered: list[tuple[str, str | None, bool]] = []
        self.notifier = SimpleNamespace(send=self.send, _resolve_token=lambda: "TOKEN")

    def is_update_allowed(self, update: tg.TelegramUpdate) -> bool:
        return True

    def answer_callback_query(
        self, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> tg.TelegramSendResult:
        self.answered.append((callback_query_id, text, show_alert))
        return tg.TelegramSendResult(ok=True)

    def send(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> tg.TelegramSendResult:
        del parse_mode
        self.sent.append((chat_id, text, reply_markup))
        return tg.TelegramSendResult(ok=True)


class _KeyboardModule:
    @staticmethod
    def home_menu_keyboard() -> dict[str, Any]:
        return {"home": True}

    @staticmethod
    def data_jobs_keyboard() -> dict[str, Any]:
        return {"data": True}

    @staticmethod
    def reports_keyboard() -> dict[str, Any]:
        return {"reports": True}

    @staticmethod
    def risk_keyboard() -> dict[str, Any]:
        return {"risk": True}

    @staticmethod
    def strategy_actions_keyboard(strategy_id: str) -> dict[str, Any]:
        return {"strategy": strategy_id}

    @staticmethod
    def confirm_delete_keyboard(strategy_id: str) -> dict[str, Any]:
        return {"confirm": strategy_id}


class _ShortConfirmDelete(str):
    def __new__(cls) -> "_ShortConfirmDelete":
        return str.__new__(cls, "op:strat:confirm_delete:")

    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
        if sep == ":":
            return ["op", "strat", "confirm_delete"]
        return super().split(sep, maxsplit)


def _bar(open_time: int = 0, *, timeframe: str = "1m", symbol: str = "BTCUSDT") -> Bar:
    tf = parse_timeframe(timeframe)
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol=symbol),
        timeframe=tf,
        time=open_time,
        time_close=open_time + (tf.duration_ms or 0),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1.0,
        closed=True,
    )


def _strategy() -> StrategyInstance:
    return StrategyInstance(
        strategy_id="strategy-flat",
        name="strategy-flat",
        pine_id="pine-flat",
        artifact_id="artifact-flat",
        params_json="{}",
        params_hash="hash-flat",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode="paper",
        enabled=True,
    )


def test_telegram_env_alias_skips_non_assignment_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_dir = tmp_path / ".openpine"
    env_dir.mkdir()
    (env_dir / "env").write_text(
        "NO_EQUALS\nexport ALSO_NO_EQUALS\nOPENPINE_PASS56_ENV=value56\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENPINE_PASS56_ENV", raising=False)

    spec = importlib.util.spec_from_file_location(
        "_openpine_telegram_pass56_alias", Path(tg.__file__)
    )
    assert spec is not None and spec.loader is not None
    alias_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, alias_module)
    spec.loader.exec_module(alias_module)

    assert os.environ["OPENPINE_PASS56_ENV"] == "value56"


@pytest.mark.asyncio
async def test_telegram_run_zero_processed_then_stops() -> None:
    plugin = _TelegramPlugin()
    handler = tg.TelegramBotHandler(plugin, commands_module=_KeyboardModule, cli_path="openpine")
    calls = 0

    def poll_once() -> int:
        nonlocal calls
        calls += 1
        assert handler._loop is not None
        handler._loop.call_soon_threadsafe(handler._stop_event.set)
        return 0

    handler._poll_once = poll_once  # type: ignore[method-assign]

    await handler.run(poll_interval=0.01)

    assert calls == 1


def test_telegram_process_update_render_run_cli_and_edit_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _TelegramPlugin()
    handler = tg.TelegramBotHandler(plugin, commands_module=_KeyboardModule, cli_path="openpine")

    handler._process_update(
        tg.TelegramUpdate(
            update_id=1,
            message=tg.TelegramMessage(chat_id="42", text="plain text", message_id=1),
        )
    )
    assert plugin.sent == []

    handler._render_menu_callback("op:strategy:", "42", message_id=None)
    handler._render_menu_callback("op:strategy:start:", "42", message_id=None)
    handler._render_menu_callback(_ShortConfirmDelete(), "42", message_id=None)
    assert [sent[2] for sent in plugin.sent[-3:]] == [
        {"home": True},
        {"home": True},
        {"home": True},
    ]

    monkeypatch.setattr(tg, "_run_cli_argv", lambda argv, cli_path="openpine": "<ok>")
    handler._run_cli_and_respond(["version"], "42", "op:strategy:start")
    assert plugin.sent[-1] == ("42", "&lt;ok&gt;", None)

    urlopen_calls: list[tuple[Any, int]] = []

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(req: Any, timeout: int) -> _Response:
        urlopen_calls.append((req, timeout))
        return _Response()

    monkeypatch.setattr(tg.urllib.request, "urlopen", fake_urlopen)

    handler._edit_message_reply_markup("42", 99, {"inline_keyboard": []})

    assert urlopen_calls and urlopen_calls[0][1] == 10


@pytest.mark.asyncio
async def test_telegram_daemon_stop_without_handler_or_task() -> None:
    svc = TelegramDaemonService()

    await svc._on_stop(timeout=0.01)

    assert svc._handler is None
    assert svc._poll_task is None


def test_scheduler_dedupe_stale_map_failed_without_running_and_expired_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = JobScheduler()
    scheduler._idempotency_map["same"] = "missing-job-id"

    deduped = scheduler.enqueue(Job(job_type=JobType.BACKTEST, idempotency_key="same"))
    assert scheduler.get_job(deduped.id) is deduped
    assert scheduler._idempotency_map["same"] == deduped.id

    no_running_key = scheduler.enqueue(Job(job_type=JobType.REPORT))
    scheduler.mark_failed(no_running_key.id, "boom")
    assert no_running_key.status == JobStatus.FAILED

    monkeypatch.setattr("openpine.jobs.scheduler.time.time", lambda: 1000.0)
    assert scheduler.acquire_lock("resource", "owner-a", ttl_seconds=-1) is True
    assert scheduler.acquire_lock("resource", "owner-b", ttl_seconds=60) is True
    assert scheduler._locks["resource"][0] == "owner-b"


@pytest.mark.asyncio
async def test_ws_broadcast_progress_missing_operation_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ConnectionManager()

    async def fail_broadcast(data: dict[str, Any]) -> None:
        raise AssertionError(f"unexpected broadcast: {data!r}")

    monkeypatch.setattr(manager, "broadcast", fail_broadcast)

    await manager.broadcast_progress("missing-operation")


class _EmptyRegistry:
    def list_strategies(self) -> list[StrategyInstance]:
        return []


class _RecordingOrchestrator:
    def __init__(self) -> None:
        self.closed: list[tuple[Bar, str, str, str]] = []

    def on_candle_closed(
        self, bar: Bar, *, instrument_key: str, timeframe: str, source: str
    ) -> None:
        self.closed.append((bar, instrument_key, timeframe, source))

    def get_bars(self, query: object) -> list[Bar]:
        raise AssertionError(f"no aggregation expected: {query!r}")


def test_worker_heartbeat_unregistered_and_fanout_without_source_persist() -> None:
    pool = WorkerPool(JobScheduler())
    pool.worker_heartbeat("unregistered")
    assert pool.get_status()["heartbeats"].keys() == {"unregistered"}
    assert pool.get_status()["active_workers"] == 0

    orchestrator = _RecordingOrchestrator()
    fanout = StrategyBarFanout(
        registry=_EmptyRegistry(),
        orchestrator=orchestrator,
        scheduler=JobScheduler(),
        config=StrategyBarFanoutConfig(persist_source=False),
    )

    result = fanout.process_source_bar(_bar(0, timeframe="1m"))

    assert result.strategies == 0
    assert result.targets[0].status == FanoutStatus.NO_STRATEGIES
    assert orchestrator.closed == []


def test_strategy_job_executor_records_flat_position_branch() -> None:
    ledger = SimpleNamespace(positions=[])

    def upsert_position(position: Any) -> None:
        ledger.positions.append(position)

    ledger.upsert_position = upsert_position
    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        ledger=ledger,
        runtime_adapter=SimpleNamespace(),
        strategy_loader=lambda strategy: object,
        runtime_data_provider="provider",
    )
    position = SimpleNamespace(
        size=0,
        direction="flat",
        avg_price=None,
        realized_profit=None,
        open_profit=None,
    )

    executor._record_position(
        _strategy(),
        LedgerSource.PAPER,
        _bar(0, timeframe="1m"),
        resume_state=SimpleNamespace(broker_state=SimpleNamespace(position=position)),
        raw_result=SimpleNamespace(open_trades=[]),
    )

    assert ledger.positions[0].side == PositionSide.FLAT
    assert ledger.positions[0].qty == 0.0


def test_openpine_main_guard_false_branch() -> None:
    import openpine

    runpy.run_path(
        str(Path(openpine.__file__).with_name("__main__.py")),
        run_name="_openpine_pass56_not_main",
    )
