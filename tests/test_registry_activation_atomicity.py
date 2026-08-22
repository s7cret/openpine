from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

from openpine.registry.strategies import SQLiteStrategyRegistry


def _registered_pair(tmp_path):
    db_path = tmp_path / "strategies.sqlite"
    activation = SQLiteStrategyRegistry(db_path)
    concurrent = SQLiteStrategyRegistry(db_path)
    strategy = activation.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        params={},
        name="atomic-activation",
    )
    return db_path, activation, concurrent, strategy.strategy_id


def _pause_after_circuit_read(monkeypatch, registry):
    checked = threading.Event()
    release = threading.Event()
    original = registry._worker_circuit_state_locked

    def paused_read():
        state = original()
        checked.set()
        if not release.wait(timeout=5):
            raise TimeoutError("activation test did not release circuit read")
        return state

    monkeypatch.setattr(registry, "_worker_circuit_state_locked", paused_read)
    return checked, release


def _run_in_thread(target):
    errors: list[BaseException] = []

    def guarded():
        try:
            target()
        except BaseException as exc:  # pragma: no branch - assertion reports thread errors
            errors.append(exc)

    thread = threading.Thread(target=guarded)
    thread.start()
    return thread, errors


def _durable_state(db_path, strategy_id):
    with closing(sqlite3.connect(db_path)) as conn, conn:
        strategy = conn.execute(
            "SELECT enabled, archived FROM strategy_instances WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        circuit = conn.execute(
            "SELECT is_open FROM runtime_circuits WHERE name = 'background_worker'"
        ).fetchone()
    assert strategy is not None
    assert circuit is not None
    return bool(strategy[0]), bool(strategy[1]), bool(circuit[0])


def test_patch_can_archive_an_enabled_strategy(tmp_path) -> None:
    db_path, registry, observer, strategy_id = _registered_pair(tmp_path)
    try:
        registry.activate_strategy(strategy_id, status="running")
        registry.patch_strategy_atomic(strategy_id, {"archived": True})
        assert _durable_state(db_path, strategy_id) == (False, True, False)
    finally:
        registry.close()
        observer.close()


def test_activation_and_circuit_trip_are_one_cross_connection_transaction(
    tmp_path, monkeypatch
) -> None:
    db_path, activation, fail_safe, strategy_id = _registered_pair(tmp_path)
    checked, release = _pause_after_circuit_read(monkeypatch, activation)
    try:
        activate_thread, activate_errors = _run_in_thread(
            lambda: activation.activate_strategy(strategy_id, status="running")
        )
        assert checked.wait(timeout=2)

        trip_done = threading.Event()
        trip_thread, trip_errors = _run_in_thread(
            lambda: (fail_safe.trip_worker_circuit("test_trip"), trip_done.set())
        )
        trip_done.wait(timeout=1)
        release.set()

        activate_thread.join(timeout=5)
        trip_thread.join(timeout=5)
        assert not activate_thread.is_alive()
        assert not trip_thread.is_alive()
        assert activate_errors == []
        assert trip_errors == []
        assert _durable_state(db_path, strategy_id) == (False, False, True)
    finally:
        release.set()
        activation.close()
        fail_safe.close()


def test_activation_and_archive_are_one_cross_connection_transaction(
    tmp_path, monkeypatch
) -> None:
    db_path, activation, archiver, strategy_id = _registered_pair(tmp_path)
    checked, release = _pause_after_circuit_read(monkeypatch, activation)
    try:
        activate_thread, activate_errors = _run_in_thread(
            lambda: activation.activate_strategy(strategy_id, status="running")
        )
        assert checked.wait(timeout=2)

        archive_done = threading.Event()
        archive_thread, archive_errors = _run_in_thread(
            lambda: (archiver.set_archived(strategy_id, True), archive_done.set())
        )
        archive_done.wait(timeout=1)
        release.set()

        activate_thread.join(timeout=5)
        archive_thread.join(timeout=5)
        assert not activate_thread.is_alive()
        assert not archive_thread.is_alive()
        assert activate_errors == []
        assert archive_errors == []
        assert _durable_state(db_path, strategy_id) == (False, True, False)
    finally:
        release.set()
        activation.close()
        archiver.close()
