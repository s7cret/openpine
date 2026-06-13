from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from openpine.notifications import telegram as tg
from openpine.state.errors import SnapshotNotFoundError
from openpine.state.store import SavePolicy, SnapshotMetadata, StateStore, StrategyState
from openpine.storage import adapters as sa


def _state(strategy_id: str = "s1", bar_time: int = 1) -> StrategyState:
    return StrategyState(
        strategy_id=strategy_id,
        artifact_id="a1",
        params_hash="h1",
        instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        timeframe={"canonical": "1m"},
        state_data={"x": 1},
        bar_time=bar_time,
        saved_at=0,
    )


def test_state_store_interval_invalid_delete_and_metadata_edges(tmp_path: Path):
    store = StateStore(tmp_path, save_policy=SavePolicy.INTERVAL, save_interval_bars=2)
    state = _state(bar_time=10)
    assert store.save_snapshot(state, failed_bar=True) is None
    assert store.save_snapshot(state) is None
    assert store.save_snapshot(_state(bar_time=15)) is None
    meta1 = store.save_snapshot(_state(bar_time=20), reason="manual", data_fingerprint="fp")
    assert meta1 is not None and meta1.reason == "manual"
    assert store.get_save_policy() == (SavePolicy.INTERVAL, 2)
    store.set_save_policy(SavePolicy.EVERY_BAR)
    meta2 = store.save_snapshot(_state(bar_time=30))
    assert meta2 is not None
    assert meta1.status == "superseded" and meta2.status == "active"
    assert store.latest_snapshot_metadata("s1").snapshot_id == meta2.snapshot_id
    assert store.load_snapshot("s1").bar_time == 30
    assert store.load_latest_compatible("s1", artifact_id="a1", params_hash="h1").bar_time == 30
    assert store.load_latest_compatible("s1", artifact_id="missing") is None
    assert store.load_runtime_snapshot("s1") == {"x": 1}
    assert store.get_save_policy() == (SavePolicy.EVERY_BAR, 1)
    store.set_save_policy(SavePolicy.ON_REQUEST, interval_bars=9)
    assert store.get_save_policy() == (SavePolicy.ON_REQUEST, 9)
    store.mark_invalid("s1", since_bar_time=25)
    assert store.latest_snapshot_metadata("s1") is None
    store.delete_snapshot(meta2.snapshot_id)
    with pytest.raises(SnapshotNotFoundError):
        store.delete_snapshot("nope")

    # Persisted metadata load with corrupt file is fail-soft.
    (tmp_path / "snapshots.index.json").write_text("not-json", encoding="utf-8")
    reloaded = StateStore(tmp_path)
    assert reloaded.list_snapshots("s1") == []
    d = meta1.to_dict()
    assert SnapshotMetadata.from_dict(d).to_dict() == d
    payload = state.to_payload()
    assert StrategyState.from_payload(payload).strategy_id == state.strategy_id
    assert state.checksum()


def test_storage_backend_adapters_success_and_error_edges(monkeypatch, tmp_path: Path):
    sqlite = sa.SQLiteControlStorageAdapter(tmp_path / "control.sqlite")
    assert sqlite.available() is True
    assert sqlite.health_check().health.value == "available"
    sqlite.close()

    parquet = sa.ParquetDataLakeAdapter(tmp_path / "lake")
    assert parquet.available() is True
    parquet.write_ohlcv("BTCUSDT", "1m", [{"time": 1, "close": 2.0}])
    assert parquet.read_ohlcv("BTCUSDT", "1m", 0, 10)
    assert parquet.health_check().health.value == "available"
    (tmp_path / "blocked").write_text("x", encoding="utf-8")
    assert sa.ParquetDataLakeAdapter(tmp_path / "blocked").available() is False

    duck_missing = sa.DuckDBAnalyticsAdapter(tmp_path / "x.duckdb", tmp_path / "lake")
    monkeypatch.setattr(duck_missing, "_duckdb_available", False)
    assert duck_missing.available() is False
    assert "duckdb" in duck_missing.health_check().error
    with pytest.raises(RuntimeError):
        duck_missing.query("select 1")

    duck_mod = ModuleType("duckdb")
    duck_mod.__version__ = "1.0"
    class Conn:
        def __init__(self):
            self.closed = False
        def execute(self, *args, **kwargs):
            return self
        def fetchone(self):
            return (0,)
        def fetchall(self):
            return [(1,)]
        def close(self):
            self.closed = True
    duck_mod.connect = lambda path: Conn()
    monkeypatch.setitem(sys.modules, "duckdb", duck_mod)
    duck = sa.DuckDBAnalyticsAdapter(tmp_path / "ok.duckdb", tmp_path / "lake")
    monkeypatch.setattr(duck, "_duckdb_available", True)
    assert duck.available() is True
    assert duck.health_check().version == "1.0"
    assert duck.query("select 1") == [(1,)]
    duck.close()

    pg = sa.PostgresControlStorageAdapter(host="localhost", port=5432, dbname="db", user="u", password="p")
    monkeypatch.setattr(pg, "_psycopg_available", False)
    assert pg.available() is False
    assert "psycopg" in pg.health_check().error


def test_telegram_bot_handler_more_edges(monkeypatch, tmp_path: Path):
    sent: list[tuple[str, str]] = []
    edited: list[object] = []

    class Notifier:
        def send(self, chat_id, text, reply_markup=None):
            sent.append((str(chat_id), text))
        def _resolve_token(self):
            return "TOKEN"

    class Plugin:
        def __init__(self):
            self.notifier = Notifier()
            self.config = SimpleNamespace(enabled=True, chat_allowlist=["1"])
        def is_update_allowed(self, update):
            return update.chat_id == "1"
        def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
            edited.append((callback_query_id, text, show_alert))
        def get_updates(self, offset=None, timeout=0, limit=100, allowed_updates=None):
            return [tg.TelegramUpdate(1, message=tg.TelegramMessage(chat_id="1", text="/status"))]
        def get_file(self, file_id):
            return {"ok": True, "result": {"file_path": "files/a.pine"}}
        def download_file(self, file_path):
            return b"//@version=6\nindicator('x')\n"

    commands = ModuleType("commands")
    commands.map_callback_data = lambda data: ["status"] if data != "bad" else (_ for _ in ()).throw(ValueError("bad callback"))
    commands.map_telegram_command = lambda text: ["status"] if text != "/bad" else (_ for _ in ()).throw(ValueError("bad command"))
    commands.home_menu_keyboard = lambda: {"home": True}
    commands.strategy_actions_keyboard = lambda sid: {"strategy": sid}
    commands.reports_keyboard = lambda: {"reports": True}
    commands.risk_keyboard = lambda: {"risk": True}
    commands.data_jobs_keyboard = lambda: {"data": True}
    commands.strategy_list_keyboard = lambda strategies: {"strategies": strategies}
    commands.pine_list_keyboard = lambda sources: {"pine": sources}
    commands.confirm_delete_keyboard = lambda sid: {"delete": sid}

    monkeypatch.setattr(tg, "_run_cli_argv", lambda argv, cli_path="openpine": json.dumps([{"id": "s"}]) if "list" in argv else "<ok>")
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"ok": false, "description": "bad edit"}'
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Resp())

    handler = tg.TelegramBotHandler(Plugin(), commands_module=commands)
    assert handler._poll_once() == 1
    assert handler._offset == 2
    handler._process_update(tg.TelegramUpdate(2, message=tg.TelegramMessage(chat_id="2", text="/status")))
    handler._process_update(tg.TelegramUpdate(3, callback_query=tg.TelegramCallbackQuery(id="cb", data="op:reports:x", chat_id="1", message_id=10)))
    handler._process_update(tg.TelegramUpdate(4, callback_query=tg.TelegramCallbackQuery(id="cb2", data="bad", chat_id="1")))
    handler._process_update(tg.TelegramUpdate(5, message=tg.TelegramMessage(chat_id="1", text="/bad")))
    handler._process_update(tg.TelegramUpdate(6, message=tg.TelegramMessage(chat_id="1", document={"file_id": "f", "file_name": "x.exe"})))
    handler._process_update(tg.TelegramUpdate(7, message=tg.TelegramMessage(chat_id="1", document={"file_id": "f", "file_name": "x.pine"})))
    handler._send_message("1", "hello")
    handler._edit_message_reply_markup("1", 10, {"k": True})
    assert sent
    asyncio.run(handler.stop())
