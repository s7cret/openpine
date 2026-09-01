from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe


def _bar(time_ms: int = 60_000) -> Bar:
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        time=time_ms,
        time_close=time_ms + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        closed=True,
    )


class _Obj(SimpleNamespace):
    pass


def test_telegram_env_autoload_and_bot_error_edges(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    env_dir = tmp_path / ".openpine"
    env_dir.mkdir()
    (env_dir / "env").write_text(
        "# ignored\nexport OPENPINE_PASS44_TOKEN=tok44\nOPENPINE_PASS44_OTHER=value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENPINE_PASS44_TOKEN", raising=False)
    monkeypatch.delenv("OPENPINE_PASS44_OTHER", raising=False)

    import openpine.notifications.telegram as telegram

    spec = importlib.util.spec_from_file_location(
        "_openpine_telegram_pass44_alias", Path(telegram.__file__)
    )
    assert spec is not None and spec.loader is not None
    alias_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, alias_module)
    spec.loader.exec_module(alias_module)
    assert os.environ["OPENPINE_PASS44_TOKEN"] == "tok44"
    assert os.environ["OPENPINE_PASS44_OTHER"] == "value"

    class Transport:
        def get_updates(self, **kwargs):
            return {"ok": True, "result": []}

        def answer_callback_query(self, **kwargs):
            return telegram.TelegramSendResult(ok=True)

        def send(self, **kwargs):
            return telegram.TelegramSendResult(ok=True)

        def get_file(self, **kwargs):
            return {"ok": True, "result": {"file_path": "scripts/a.pine"}}

        def download_file(self, **kwargs):
            raise RuntimeError("download boom")

    config = telegram.TelegramPluginConfig(
        enabled=True,
        token_ref="env:OPENPINE_PASS44_TOKEN",
        chat_allowlist=["42"],
    )
    plugin = telegram.TelegramCommandPlugin(config=config, transport=Transport())

    class Commands:
        @staticmethod
        def home_menu_keyboard():
            return {"home": True}

        @staticmethod
        def data_jobs_keyboard():
            return {"data": True}

        @staticmethod
        def reports_keyboard():
            return {"reports": True}

        @staticmethod
        def risk_keyboard():
            return {"risk": True}

        @staticmethod
        def strategy_actions_keyboard(strategy_id):
            return {"strategy": strategy_id}

        @staticmethod
        def confirm_delete_keyboard(strategy_id):
            return {"confirm": strategy_id}

        @staticmethod
        def strategy_list_keyboard(items):
            return {"strategies": items}

        @staticmethod
        def pine_list_keyboard(items):
            return {"pine": items}

        @staticmethod
        def map_callback_data(data):
            if data == "bad":
                raise ValueError("bad callback")
            if data == "menu":
                return []
            return ["version"]

        @staticmethod
        def map_telegram_command(text):
            raise ValueError("bad command")

    sent: list[tuple[str, str, object]] = []

    def fake_send(chat_id, text, reply_markup=None):
        sent.append((chat_id, text, reply_markup))

    plugin.notifier.send = fake_send
    handler = telegram.TelegramBotHandler(plugin, commands_module=Commands, cli_path="openpine")

    handler._handle_callback_query(telegram.TelegramUpdate(update_id=1))
    handler._handle_callback_query(
        telegram.TelegramUpdate(
            update_id=2,
            callback_query=telegram.TelegramCallbackQuery(id="cq", data="menu", chat_id=None),
        )
    )
    handler._handle_callback_query(
        telegram.TelegramUpdate(
            update_id=3,
            callback_query=telegram.TelegramCallbackQuery(id="cq2", data="bad", chat_id="42"),
        )
    )
    handler._handle_command_message(telegram.TelegramUpdate(update_id=4))
    handler._handle_command_message(
        telegram.TelegramUpdate(update_id=5, message=telegram.TelegramMessage(chat_id="42", text="/bad"))
    )
    handler._handle_document_message(telegram.TelegramUpdate(update_id=6))
    handler._handle_document_message(
        telegram.TelegramUpdate(
            update_id=7,
            message=telegram.TelegramMessage(chat_id=None, document={"file_id": "f", "file_name": "x.pine"}),
        )
    )
    handler._handle_document_message(
        telegram.TelegramUpdate(
            update_id=8,
            message=telegram.TelegramMessage(chat_id="42", document={"file_id": "f", "file_name": "x.pine"}),
        )
    )

    plugin.notifier.send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("send boom"))
    handler._send_message("42", "text")
    assert sent


@pytest.mark.asyncio
async def test_telegram_run_loop_poll_exception_timeout_and_stop(monkeypatch):
    import openpine.notifications.telegram as telegram

    class Plugin:
        notifier = SimpleNamespace(send=lambda *a, **k: None)

        def get_updates(self, **kwargs):
            return []

    handler = telegram.TelegramBotHandler(Plugin(), commands_module=SimpleNamespace(), cli_path="openpine")
    calls = {"count": 0}

    def poll_once():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("poll boom")
        handler._stop_event.set()
        return 1

    handler._poll_once = poll_once
    await handler.run(poll_interval=0.001)
    await handler.stop()
    assert calls["count"] >= 1


def test_optimizer_adapter_remaining_fail_closed_and_metadata_edges(tmp_path):
    from openpine.optimizer.adapter import (
        LocalOptimizerAdapter,
        OptimizerRunConfig,
        OptimizerService,
    )

    empty_module = ModuleType("optimizer_empty")
    adapter = LocalOptimizerAdapter(empty_module)
    detection = adapter.detect()
    assert detection.available is False
    assert "missing" in detection.reason

    ref = adapter.start_optimization(OptimizerRunConfig(strategy_id="s", trials=1))
    failed = adapter.get_result(ref.optimization_id)
    assert failed.status == "failed"
    with pytest.raises(KeyError):
        adapter.get_result("missing")

    cfg = OptimizerRunConfig(strategy_id="s", trials=1, artifact_id="a", data_query={"symbol": "BTC"})
    none_adapter = LocalOptimizerAdapter(empty_module)
    none_adapter._module = None
    assert none_adapter._run_local_optimizer("opt_none", cfg).metrics["failure_reason"]
    assert LocalOptimizerAdapter(empty_module)._run_local_optimizer("opt_no_params", cfg).status == "failed"
    assert LocalOptimizerAdapter(empty_module)._run_local_optimizer(
        "opt_no_engine",
        OptimizerRunConfig(strategy_id="s", trials=1, artifact_id="a", data_query={}, parameters=({"name": "x"},)),
    ).status == "failed"

    class ResultType:
        pass

    class RaisingModule(ModuleType):
        OptimizerRunResult = ResultType
        Trial = object
        OptimizerConfig = object
        BacktestEngineRunnerAdapter = object

        def Parameter(self, *args, **kwargs):
            return (args, kwargs)

        def optimize(self, *args, **kwargs):
            raise RuntimeError("optimizer failed")

    raising_module = RaisingModule("optimizer_raise")
    raising_adapter = LocalOptimizerAdapter(raising_module)
    raising_adapter._module = raising_module
    result = raising_adapter._run_local_optimizer(
        "opt_raise",
        OptimizerRunConfig(
            strategy_id="s",
            trials=1,
            artifact_id="a",
            data_query={"x": 1},
            parameters=({"name": "x", "default": 1},),
            engine_factory=lambda: object(),
            strategy=object(),
            bars=(object(),),
            output_dir=tmp_path,
        ),
    )
    assert "optimizer call failed" in result.metrics["failure_reason"]

    norm_adapter = LocalOptimizerAdapter(empty_module)
    unsupported = norm_adapter._normalize_optimizer_result("opt_bad", cfg, object(), empty_module, object())
    assert unsupported.status == "failed"

    diag_obj = SimpleNamespace(code="D1", level="warn")
    trial = SimpleNamespace(
        id="t",
        status="completed",
        diagnostics=(diag_obj, "plain"),
        metrics={"m": 1},
    )
    meta = norm_adapter._trial_metadata(trial)
    assert meta["diagnostics"][0]["code"] == "D1"
    assert meta["diagnostics"][1] == "plain"
    assert norm_adapter._failed_result("opt_reasonless", cfg, None).metrics == {"optimizer_adapter": "local"}
    assert OptimizerService().validate_config("s", 0).status == "invalid"




def test_backtest_storage_remaining_artifact_and_lookup_edges(monkeypatch, tmp_path):
    from openpine.storage import backtest_storage as bs
    from openpine.storage.backtest_dto import BacktestMetricsSummary

    store = object.__new__(bs.BacktestResultStore)
    store._data_dir = tmp_path / "backtests"
    store._data_dir.mkdir()

    class Cursor:
        description = [("run_id",), ("strategy_id",)]

        def __init__(self, rows=(), row=None):
            self._rows = list(rows)
            self._row = row

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._row

    class FakeStorage:
        def __init__(self):
            self.calls = []
            self.fetchone_row = None
            self.fetchall_rows = []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return Cursor(self.fetchall_rows, self.fetchone_row)

        def execute_many(self, *args, **kwargs):
            self.calls.append(("many", args, kwargs))

        def commit(self):
            self.calls.append(("commit",))

        def transaction(self):
            class Tx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

            return Tx()

    fake = FakeStorage()
    store._storage = fake
    store._insert_trade_db_rows(run_id="r", strategy_id="s", trades=[], now=1)
    assert not any(call and call[0] == "many" for call in fake.calls)

    row = store._artifact_db_row(
        run_id="r",
        strategy_id="s",
        artifact_type=bs.ARTIFACT_TYPE_TRADES,
        path=tmp_path / "missing.parquet",
        now=1,
    )
    assert row[6] is None

    fake.fetchall_rows = []
    fake.fetchone_row = None
    assert store.get_metrics("missing") is None
    assert store._get_strategy_id("missing") == ""

    fake.fetchall_rows = [(str(tmp_path / "does_not_exist.json"),)]
    assert store.get_metrics("missing_file") is None

    tmp_artifacts = tmp_path / "tmp"
    run_dir = tmp_path / "run"
    tmp_artifacts.mkdir()
    run_dir.mkdir()
    metrics = BacktestMetricsSummary(final_equity=101.0, net_profit=1.0, trades_total=0)
    artifacts = store._write_result_artifacts(
        tmp_dir=tmp_artifacts,
        run_dir=run_dir,
        run_id="r",
        strategy_id="s",
        result=SimpleNamespace(symbol="BTC", timeframe="1m"),
        metrics=metrics,
        equity_curve=None,
        trades=[],
        bar_outputs=[{"bar_time": 1, "value": 2.0}],
        plots=[(1, 0, 5.0, "plot")],
        now=1,
    )
    assert bs.ARTIFACT_TYPE_BAR_OUTPUTS in artifacts
    assert bs.ARTIFACT_TYPE_PLOT_OUTPUTS in artifacts
    assert (tmp_artifacts / "plot_outputs.csv").exists()
