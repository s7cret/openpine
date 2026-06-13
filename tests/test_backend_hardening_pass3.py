from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from marketdata_provider.contracts import InstrumentKey, parse_timeframe

from openpine.config.model import OpenPineConfig
from openpine.contracts import (
    CompileArtifact,
    PineSource,
    RuntimeStatus,
    Status,
    StrategyInstance,
    StrategyRuntimeError,
)
from openpine.daemon.refresh_service import MarketDataRefreshService
from openpine.daemon.service import DaemonService, ServiceState
from openpine.recovery.rebuild import StateRebuilder
from openpine.state.errors import StateInconsistencyError
from openpine.storage.backup import (
    _checkpoint_sqlite,
    _config_safe_dict,
    _redact_sensitive,
    backup_openpine,
    restore_openpine,
    verify_openpine,
)


@dataclass
class Snapshot:
    strategy_id: str
    bar_time: int
    instrument_key: InstrumentKey
    timeframe: object
    status: str = "active"
    state_data: dict | None = None


class FakeStateStore:
    def __init__(self, snapshots: list[Snapshot]) -> None:
        self.snapshots = snapshots
        self.saved: list[tuple[Snapshot, str, bool]] = []

    def list_snapshots(self, strategy_id: str) -> list[Snapshot]:
        return [s for s in self.snapshots if s.strategy_id == strategy_id]

    def load_snapshot(self, strategy_id: str):
        matches = self.list_snapshots(strategy_id)
        return matches[-1] if matches else None

    def save_snapshot(self, state: Snapshot, reason: str, failed_bar: bool):
        self.saved.append((state, reason, failed_bar))
        return state


class FakeBars:
    def __init__(self) -> None:
        self.keywords: dict | None = None

    def get_bars(self, **kwargs):
        self.keywords = kwargs
        return [SimpleNamespace(time=200), SimpleNamespace(time=300)]


class FakeEngine:
    def __init__(self) -> None:
        self.processed: list[int] = []

    def process_next_bar(self, *, state, bar) -> None:
        self.processed.append(bar.time)


class RecordingService(DaemonService):
    def __init__(self, *, fail_start: bool = False, fail_stop: bool = False) -> None:
        super().__init__("recording")
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.started = 0
        self.stopped = 0

    async def _on_start(self) -> None:
        self.started += 1
        if self.fail_start:
            raise RuntimeError("start failed")

    async def _on_stop(self, timeout: float) -> None:
        self.stopped += 1
        if self.fail_stop:
            raise RuntimeError("stop failed")


@pytest.mark.asyncio
async def test_daemon_service_lifecycle_and_errors():
    service = RecordingService()
    assert service.state == ServiceState.STOPPED
    await service.stop()
    await service.start()
    assert service.is_running() is True
    await service.start()
    assert service.started == 1
    await service.stop(timeout=0.2)
    assert service.state == ServiceState.STOPPED
    assert "recording" in repr(service)

    failing = RecordingService(fail_start=True)
    with pytest.raises(RuntimeError, match="start failed"):
        await failing.start()
    assert failing.state == ServiceState.STOPPED

    stopping = RecordingService(fail_stop=True)
    await stopping.start()
    await stopping.stop(timeout=0.2)
    assert stopping.state == ServiceState.STOPPED


def test_contract_models_roundtrip():
    key = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    instance = StrategyInstance(
        id="s1",
        artifact_id="a1",
        params_hash="p1",
        instrument_key=key,
        timeframe=timeframe,
        status=Status.RUNNING,
    )
    assert instance.status == Status.RUNNING
    assert RuntimeStatus.LIVE.value == "live"
    assert (
        PineSource(id="p1", name="alpha", source_text="plot(close)").version == "1.0.0"
    )
    assert (
        CompileArtifact(
            id="a1",
            source_id="p1",
            params_hash="h",
            artifact_path="x.py",
            compile_meta={},
        ).source_id
        == "p1"
    )
    err = StrategyRuntimeError(
        strategy_id="s1",
        artifact_id="a1",
        params_hash="h",
        instrument_key=key,
        timeframe=timeframe,
        bar_time=123,
        error_type="RuntimeError",
        message="boom",
        traceback_id="tb1",
    )
    assert err.strategy_status_after == Status.ERROR


def test_backup_verify_restore_and_redaction(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = OpenPineConfig(
        workspace_root=workspace, telegram={"token": "secret-token"}
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    config.sqlite_path.write_bytes(b"")
    config.duckdb_path.write_bytes(b"duck")
    for dirname in ["artifacts", "manifests", "state"]:
        path = config.data_dir / dirname
        path.mkdir(parents=True, exist_ok=True)
        (path / "item.txt").write_text(dirname, encoding="utf-8")
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text("timezone: UTC\n", encoding="utf-8")

    safe = _config_safe_dict(config)
    assert safe["plugins"]["telegram"]["token_ref"] == "<REDACTED>"
    nested = {"api_key": "abc", "children": [{"secret": "xyz"}]}
    _redact_sensitive(nested)
    assert nested == {"api_key": "<REDACTED>", "children": [{"secret": "<REDACTED>"}]}
    _checkpoint_sqlite(tmp_path / "not-a-db.sqlite")

    archive = tmp_path / "backup.tar.gz"
    backed_up = backup_openpine(archive, config)
    assert archive.exists()
    assert any("openpine.sqlite" in item for item in backed_up)
    results = verify_openpine(config)
    assert results["sqlite_exists"] is True
    assert results["duckdb_exists"] is True
    assert results["artifacts_dir_exists"] is True

    restore_dir = tmp_path / "restore"
    restore_openpine(archive, restore_dir)
    assert (restore_dir / "config" / "manifest.json").exists()
    with tarfile.open(archive, "r:gz") as tar:
        assert "config/manifest.json" in tar.getnames()

    bad_archive = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        info = tarfile.TarInfo("config/manifest.json")
        payload = b'{"schema":"unknown"}'
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="Unknown backup schema"):
        restore_openpine(bad_archive, tmp_path / "bad_restore")
    with pytest.raises(FileNotFoundError):
        restore_openpine(tmp_path / "missing.tar.gz", tmp_path / "x")


def test_state_rebuilder_rebuild_verify_and_invalidate():
    key = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    snapshot = Snapshot("s1", 100, key, parse_timeframe("1m"), state_data={"x": 1})
    store = FakeStateStore([snapshot])
    data = FakeBars()
    engine = FakeEngine()
    rebuilder = StateRebuilder(store, data, engine)
    rebuilt = rebuilder.rebuild("s1", from_bar_time=150, reason="repair")
    assert rebuilt.bar_time == 300
    assert engine.processed == [200, 300]
    assert store.saved[-1][1] == "repair"
    assert rebuilder.verify_state("s1") is True
    rebuilder.invalidate("s1", since_bar_time=200)
    assert snapshot.status == "invalid"

    with pytest.raises(StateInconsistencyError):
        StateRebuilder(FakeStateStore([])).rebuild("missing", 10)
    assert StateRebuilder(FakeStateStore([])).verify_state("missing") is False
    bad_store = FakeStateStore([Snapshot("bad", 0, key, parse_timeframe("1m"))])
    assert StateRebuilder(bad_store).verify_state("bad") is False


@pytest.mark.asyncio
async def test_marketdata_refresh_service_delegates_start_stop(monkeypatch):
    calls: list[tuple[str, float | None]] = []

    class FakeFetcher:
        def __init__(self, config=None) -> None:
            self.config = config

        def start(self) -> None:
            calls.append(("start", None))

        def stop(self, timeout: float) -> None:
            calls.append(("stop", timeout))

    monkeypatch.setattr(
        "openpine.daemon.refresh_service.PeriodicBarFetcher", FakeFetcher
    )
    service = MarketDataRefreshService(config=SimpleNamespace(interval_seconds=1))
    await service.start()
    await service.stop(timeout=0.5)
    assert calls == [("start", None), ("stop", 0.5)]


def test_daemon_package_exports():
    import openpine.daemon as daemon_pkg

    assert "DaemonService" in daemon_pkg.__all__
    assert daemon_pkg.ServiceState.RUNNING.value == "running"
