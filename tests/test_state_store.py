from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import openpine.state.store as state_store_mod
from openpine.state.errors import SnapshotNotFoundError
from openpine.state.store import StateStore, StrategyState


def _state(bar_time: int = 1704067200000) -> StrategyState:
    return StrategyState(
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        timeframe={"canonical": "1h"},
        state_data={"position": 1, "secret": "redacted-in-debug"},
        bar_time=bar_time,
        saved_at=0,
    )


def test_save_snapshot_round_trips_active_state_and_redacts_debug(tmp_path) -> None:
    store = StateStore(tmp_path)

    meta = store.save_snapshot(_state(), reason="manual")

    assert meta is not None
    assert meta.reason == "manual"
    loaded = store.load_snapshot("strategy-1")
    assert loaded is not None
    assert loaded.state_data == {"position": 1, "secret": "redacted-in-debug"}

    debug_path = (
        tmp_path / "strategy_id=strategy-1" / f"snap_{meta.snapshot_id}.debug.json"
    )
    debug_payload = json.loads(debug_path.read_text())
    assert debug_payload["runtime_state"] == "<redacted>"
    assert debug_payload["checksum"]


def test_failed_bar_does_not_write_snapshot(tmp_path) -> None:
    store = StateStore(tmp_path)

    meta = store.save_snapshot(_state(), failed_bar=True)

    assert meta is None
    assert store.list_snapshots("strategy-1") == []
    assert not (tmp_path / "strategy_id=strategy-1").exists()


def test_delete_snapshot_removes_files_and_metadata(tmp_path) -> None:
    store = StateStore(tmp_path)
    meta = store.save_snapshot(_state())
    assert meta is not None

    store.delete_snapshot(meta.snapshot_id)

    assert store.list_snapshots("strategy-1") == []
    assert not (
        tmp_path
        / "strategy_id=strategy-1"
        / f"snap_{meta.snapshot_id}.state.msgpack.zst"
    ).exists()
    assert not (
        tmp_path / "strategy_id=strategy-1" / f"snap_{meta.snapshot_id}.debug.json"
    ).exists()

    with pytest.raises(SnapshotNotFoundError):
        store.delete_snapshot(meta.snapshot_id)


def test_state_store_hydrates_metadata_for_new_process(tmp_path) -> None:
    store = StateStore(tmp_path)
    meta = store.save_snapshot(_state(), data_fingerprint="bars-sha")
    assert meta is not None

    reloaded = StateStore(tmp_path)
    loaded_meta = reloaded.latest_snapshot_metadata(
        "strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        data_fingerprint="bars-sha",
    )
    loaded_state = reloaded.load_latest_compatible(
        "strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        data_fingerprint="bars-sha",
    )

    assert loaded_meta is not None
    assert loaded_meta.snapshot_id == meta.snapshot_id
    assert loaded_state is not None
    assert loaded_state.state_data["position"] == 1


def test_state_store_rejects_strategy_id_path_traversal(tmp_path) -> None:
    store = StateStore(tmp_path / "state")
    bad_state = _state()
    bad_state.strategy_id = "x/../../escape"

    with pytest.raises(ValueError, match="escapes state storage root"):
        store.save_snapshot(bad_state, reason="manual")

    assert not (tmp_path / "escape").exists()


def test_state_store_rejects_symlink_escape_inside_root(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state_dir / "strategy_id=strategy-1").symlink_to(
        outside, target_is_directory=True
    )
    store = StateStore(state_dir)

    with pytest.raises(ValueError, match="escapes state storage root"):
        store.save_snapshot(_state(), reason="manual")


def test_runtime_snapshot_namespace_round_trips_without_pickle(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENPINE_ALLOW_PICKLE_STATE", raising=False)
    store = StateStore(tmp_path)
    payload = SimpleNamespace(bar_index=42, runtime_state={"x": 1})

    meta = store.save_runtime_snapshot(
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        timeframe={"canonical": "1h"},
        runtime_state=payload,
        bar_time=1704067200000,
        data_fingerprint="bars-sha",
    )

    assert meta is not None
    assert meta.state_encoding == "msgpack"
    loaded = StateStore(tmp_path).load_runtime_snapshot(
        "strategy-1",
        data_fingerprint="bars-sha",
    )
    assert loaded == {"bar_index": 42, "runtime_state": {"x": 1}}


def test_runtime_snapshot_pickle_save_requires_trusted_opt_in(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENPINE_ALLOW_PICKLE_STATE", raising=False)
    store = StateStore(tmp_path)
    payload = {"unsupported": {1, 2}}

    with pytest.raises(Exception, match="trusted local snapshots"):
        store.save_runtime_snapshot(
            strategy_id="strategy-1",
            artifact_id="artifact-1",
            params_hash="params-1",
            instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
            timeframe={"canonical": "1h"},
            runtime_state=payload,
            bar_time=1704067200000,
            data_fingerprint="bars-sha",
        )

    assert store.list_snapshots("strategy-1") == []


def test_state_store_msgpack_safe_rejects_unsupported_edges(monkeypatch) -> None:
    assert StateStore._msgpack_safe((SimpleNamespace(x=1),)) == [{"x": 1}]

    with pytest.raises(TypeError, match="unsupported msgpack key type"):
        StateStore._msgpack_safe({("bad",): 1})
    with pytest.raises(TypeError, match="unsupported msgpack value type"):
        StateStore._msgpack_safe(object())

    monkeypatch.setattr(state_store_mod.msgpack, "packb", lambda *_args, **_kwargs: None)
    with pytest.raises(TypeError, match="msgpack did not return bytes"):
        StateStore._pack_msgpack({"ok": True})


def test_runtime_snapshot_round_trips_pickle_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENPINE_ALLOW_PICKLE_STATE", "1")
    store = StateStore(tmp_path)
    payload = {"unsupported": {1, 2}}

    meta = store.save_runtime_snapshot(
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        timeframe={"canonical": "1h"},
        runtime_state=payload,
        bar_time=1704067200000,
        data_fingerprint="bars-sha",
    )

    assert meta is not None
    assert meta.state_encoding == "pickle"
    loaded = StateStore(tmp_path).load_runtime_snapshot(
        "strategy-1",
        data_fingerprint="bars-sha",
    )
    assert loaded == {"unsupported": {1, 2}}


def test_pickle_runtime_snapshot_requires_trusted_opt_in(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENPINE_ALLOW_PICKLE_STATE", "1")
    store = StateStore(tmp_path)
    payload = {"unsupported": {1, 2}}
    store.save_runtime_snapshot(
        strategy_id="strategy-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        instrument_key={"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        timeframe={"canonical": "1h"},
        runtime_state=payload,
        bar_time=1704067200000,
        data_fingerprint="bars-sha",
    )

    monkeypatch.delenv("OPENPINE_ALLOW_PICKLE_STATE")
    with pytest.raises(Exception, match="trusted local snapshots"):
        StateStore(tmp_path).load_runtime_snapshot(
            "strategy-1", data_fingerprint="bars-sha"
        )
