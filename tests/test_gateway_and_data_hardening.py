from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.config.model import OpenPineConfig
from openpine.data.direct_data_provider import (
    DirectBinanceDataProvider,
    _interval_to_ms,
    _to_binance_interval,
)
from openpine.export.window import ExportWindow, parse_time_ms
from openpine.gateway import deps as gateway_deps
from openpine.gateway.ws_manager import ConnectionManager
from openpine.integrations import (
    CORE_LIBRARIES,
    check_core_libraries,
    check_library,
    import_library,
)


class FakeWebSocket:
    def __init__(self, *, fail_text: bool = False, fail_json: bool = False) -> None:
        self.accepted = False
        self.texts: list[str] = []
        self.jsons: list[dict] = []
        self.fail_text = fail_text
        self.fail_json = fail_json

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        if self.fail_text:
            raise RuntimeError("send failed")
        self.texts.append(payload)

    async def send_json(self, payload: dict) -> None:
        if self.fail_json:
            raise RuntimeError("json failed")
        self.jsons.append(payload)


@pytest.mark.asyncio
async def test_connection_manager_connect_send_broadcast_and_progress(monkeypatch):
    manager = ConnectionManager()
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket(fail_text=True)
    cid1 = await manager.connect(ws1, "client-1")
    cid2 = await manager.connect(ws2, "client-2")
    assert cid1 == "client-1" and cid2 == "client-2"
    assert ws1.accepted and ws2.accepted
    assert manager.active_count == 2

    await manager.send_personal("client-1", {"type": "hello"})
    await manager.send_personal("missing", {"type": "noop"})
    assert ws1.jsons == [{"type": "hello"}]
    await manager.broadcast({"type": "broadcast", "n": 1})
    assert json.loads(ws1.texts[-1])["type"] == "broadcast"
    assert manager.active_count == 1

    monkeypatch.setattr("openpine.gateway.ws_manager.time.time", lambda: 1234.0)
    manager.update_progress("op1", "backtest", "running", pct=25.0, message="quarter")
    progress = manager.get_progress("op1")
    assert progress and progress["updated_at"] == 1234000
    assert manager.get_all_progress() == [progress]
    await manager.broadcast_progress("op1")
    manager.clear_progress("op1")
    assert manager.get_progress("op1") is None
    await manager.disconnect("client-1")
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_connection_manager_disconnects_on_personal_send_failure():
    manager = ConnectionManager()
    await manager.connect(FakeWebSocket(fail_json=True), "bad")
    await manager.send_personal("bad", {"x": 1})
    assert manager.active_count == 0


def test_direct_binance_interval_helpers_and_fetch(monkeypatch):
    assert _to_binance_interval("15") == "15m"
    assert _to_binance_interval("1H") == "1h"
    assert _to_binance_interval("2h") == "2h"
    assert _interval_to_ms("1s") == 1000
    assert _interval_to_ms("1m") == 60_000
    assert _interval_to_ms("1h") == 3_600_000
    assert _interval_to_ms("1d") == 86_400_000
    assert _interval_to_ms("1w") == 604_800_000
    assert DirectBinanceDataProvider(market="futures")._base.startswith("https://fapi")
    assert DirectBinanceDataProvider().get_bars("BTCUSDT", "1m", None, 1) == []

    payloads = [
        [
            [1000, "1", "2", "0.5", "1.5", "10"],
            [2000, "2", "3", "1.5", "2.5", "20"],
        ],
        [],
    ]

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            return json.dumps(payloads.pop(0)).encode()

    def fake_urlopen(req, timeout=10):
        return FakeResponse()

    monkeypatch.setattr(
        "openpine.data.direct_data_provider.urllib.request.urlopen", fake_urlopen
    )
    bars = DirectBinanceDataProvider(timeout=1).get_bars(
        "btcusdt", "1s", 1000, 5000, max_bars=1
    )
    assert len(bars) == 1
    assert bars[0].time == 1000 and bars[0].time_close == 2000

    def failing_urlopen(req, timeout=10):
        raise OSError("offline")

    monkeypatch.setattr(
        "openpine.data.direct_data_provider.urllib.request.urlopen", failing_urlopen
    )
    assert DirectBinanceDataProvider().get_bars("BTCUSDT", "1m", 0, 10) == []


def test_export_window_and_config_path_resolution(tmp_path: Path):
    window = ExportWindow(1000, 2000)
    assert window.contains(1000) is True
    assert window.contains(1999) is True
    assert window.contains(2000) is False
    assert window.contains(None) is False
    with pytest.raises(ValueError):
        ExportWindow(2, 2)
    assert parse_time_ms(None) is None
    assert parse_time_ms("") is None
    assert parse_time_ms("1700000000") == 1700000000000
    assert parse_time_ms("1700000000000") == 1700000000000
    assert parse_time_ms("2024-01-01T00:00:00Z") == 1704067200000

    cfg = OpenPineConfig(
        workspace_root=tmp_path,
        data_dir="data",
        config_dir="conf",
        sqlite_path="db/openpine.sqlite",
        duckdb_path="db/openpine.duckdb",
        data_cache_root="cache",
        output_root="outputs",
        db_path="db/control.sqlite",
        timezone="UTC",
    )
    assert cfg.data_dir == tmp_path / "data"
    assert cfg.data_cache_root == tmp_path / "cache"
    assert cfg.output_root == tmp_path / "outputs"
    assert cfg.db_path == tmp_path / "db/control.sqlite"
    assert cfg.config_path() == tmp_path / "conf/config.yaml"
    cfg.save()
    assert cfg.config_path().exists()


def test_gateway_dependency_getters_return_state_members():
    state = SimpleNamespace(
        pine_registry="pine",
        strategy_registry="strategy",
        backtest_store="backtests",
        account_manager="accounts",
        order_manager="orders",
        event_bus="events",
        scheduler="scheduler",
        artifact_store="artifacts",
        state_store="state_store",
        orchestrator="orchestrator",
        risk_manager="risk",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gateway=state)))
    assert gateway_deps.get_state(request) is state
    assert gateway_deps.get_pine_registry(state) == "pine"
    assert gateway_deps.get_strategy_registry(state) == "strategy"
    assert gateway_deps.get_backtest_store(state) == "backtests"
    assert gateway_deps.get_account_manager(state) == "accounts"
    assert gateway_deps.get_order_manager(state) == "orders"
    assert gateway_deps.get_event_bus(state) == "events"
    assert gateway_deps.get_scheduler(state) == "scheduler"
    assert gateway_deps.get_artifact_store(state) == "artifacts"
    assert gateway_deps.get_state_store(state) == "state_store"
    assert gateway_deps.get_orchestrator(state) == "orchestrator"
    assert gateway_deps.get_risk_manager(state) == "risk"


def test_integration_discovery_success_and_failure(monkeypatch):
    assert "pine2ast" in CORE_LIBRARIES
    assert import_library("pine2ast").__name__ == "pine2ast"
    assert check_library("pine2ast").importable is True
    assert len(check_core_libraries()) == len(CORE_LIBRARIES)
    with pytest.raises(ValueError):
        import_library("not_core")
    monkeypatch.setattr(
        "openpine.integrations.import_library",
        lambda name: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    status = check_library("pine2ast")
    assert status.importable is False and "boom" in status.error


def test_candle_storage_close_is_idempotent(tmp_path):
    from openpine.data.candle_storage import CandleStorage

    storage = CandleStorage(
        data_root=tmp_path / "data", sqlite_path=tmp_path / "index.sqlite"
    )
    storage._get_conn()
    storage.close()
    storage.close()
    assert storage._conn is None
    with storage as ctx:
        ctx._get_conn()
    assert storage._conn is None
