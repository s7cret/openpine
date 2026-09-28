"""Parent-owned contracts for sealed worker tapes, ledger publications, and supervision."""
from __future__ import annotations

import asyncio
import io
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe
from openpine.jobs import Job, JobType
from openpine.registry.strategies import StrategyInstance
from openpine.runtime.rc6_worker_runtime import (
    RC6WorkerProtocol,
    _jsonable,
    _pine_timeframe,
    _session_from_request,
    run_bulk,
)
from openpine.runtime.rc6_config import serialize_engine_config
from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.strategy_ledger import LedgerSource, PositionSide, StrategyLedger
from openpine.gateway.worker_supervisor import SupervisorConfig, worker_runtime_snapshot
from openpine.workers import strategy_job_executor as executor_module
from openpine.workers.strategy_job_executor import StrategyJobExecutor
from openpine_contracts import seal_content_hash
from tests.rc4_fixtures import HASH_B, HASH_C, STACK_HASH, execution_context


@pytest.fixture
def sealed_context() -> dict[str, object]:
    return execution_context()


def _load_body(context: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_hash": HASH_B,
        "module_hash": STACK_HASH,
        "entrypoint_module": "generated_contract_edge",
        "entrypoint_class": "GeneratedScript",
    }


def _init_body(context: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": context["run_id"],
        "run_hash": "sha256:" + "1" * 64,
        "execution_context_hash": context["content_hash"],
        "execution_context": context,
        "semantic_profile": "strict_5x",
        "capabilities": ["closed_bar", "deterministic_clock"],
    }


def test_parent_and_worker_accept_a_sealed_handshake_tape(sealed_context: dict[str, object]) -> None:
    worker = RC6WorkerProtocol(sealed_context)
    parent = RC6WorkerProtocol(sealed_context)

    hello = worker.append(
        "HELLO",
        {"worker_id": sealed_context["session_id"], "protocol_version": "2.3.0", "capabilities": ["closed_bar"]},
        0,
    )
    assert parent.accept(hello)["message_id"].endswith(":0:HELLO")

    load = parent.append("LOAD_ARTIFACT", _load_body(sealed_context), 1)
    assert worker.accept(load)["causation_id"] == hello["message_id"]

    initialized = parent.append("INIT_RUN", _init_body(sealed_context), 2)
    accepted = worker.accept(initialized)
    assert accepted["sequence"] == 2
    assert accepted["producer_version"] == "5.0.0-rc.4"
    assert worker.last_id == accepted["message_id"]


def test_worker_protocol_rejects_tampering_roles_identity_and_bad_order(
    sealed_context: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="start with HELLO"):
        RC6WorkerProtocol(sealed_context).append("LOAD_ARTIFACT", _load_body(sealed_context), 0)

    worker = RC6WorkerProtocol(sealed_context)
    hello = worker.append(
        "HELLO",
        {"worker_id": sealed_context["session_id"], "protocol_version": "2.3.0", "capabilities": ["closed_bar"]},
        0,
    )
    with pytest.raises(ValueError, match="invalid worker protocol transition"):
        worker.append("INIT_RUN", _init_body(sealed_context), 1)

    receiver = RC6WorkerProtocol(sealed_context)
    corrupted = {**hello, "content_hash": "sha256:" + "e" * 64}
    with pytest.raises(ValueError, match="content hash"):
        receiver.accept(corrupted)

    wrong_role = seal_content_hash(
        {**hello, "sender_role": "parent"}, schema_id="openpine.worker.protocol.v2"
    )
    with pytest.raises(ValueError, match="sender role mismatch"):
        receiver.accept(wrong_role)

    receiver.accept(hello)
    forged_load = seal_content_hash(
        {
            **receiver.append("LOAD_ARTIFACT", _load_body(sealed_context), 1),
            "producer_commit": "f" * 40,
        },
        schema_id="openpine.worker.protocol.v2",
    )
    peer = RC6WorkerProtocol(sealed_context)
    peer.accept(hello)
    with pytest.raises(ValueError, match="identity mismatch"):
        peer.accept(forged_load)


def test_worker_runtime_rejects_malformed_session_requests_and_serializes_boundary_values(
    sealed_context: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="identities are malformed"):
        _session_from_request({"execution_context": [], "generated_artifact": {}, "source": "pass"})
    with pytest.raises(ValueError, match="source is required"):
        _session_from_request({"execution_context": sealed_context, "generated_artifact": {}, "source": None})
    with pytest.raises(ValueError, match="version context is required"):
        _session_from_request(
            {"execution_context": sealed_context, "generated_artifact": {}, "source": "pass"}
        )
    with pytest.raises(ValueError, match="timeframe"):
        _pine_timeframe(1)

    @dataclass
    class Receipt:
        code: str
        values: tuple[int, int]

    assert _jsonable(Receipt("ok", (1, 2))) == {"code": "ok", "values": [1, 2]}
    assert _jsonable({"labels": {"one", "two"}})["labels"] in (["one", "two"], ["two", "one"])
    assert _jsonable(object()).startswith("<object object at ")


def _bulk_request_and_frames() -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
    """Build a parent-authored handshake from an actual compiled RC6 artifact."""
    from rc6_tests.test_rc6_bulk_execution import bulk_case
    from openpine.runtime.worker_capabilities import WORKER_CAPABILITIES

    compiled, context, config = bulk_case.__wrapped__()
    request = {
        "generated_artifact": compiled.generated_artifact,
        "execution_context": context,
        "source": compiled.python_code,
        "engine_config": serialize_engine_config(config, "strict_5x"),
        "params": {},
    }
    emitted = RC6WorkerProtocol(context)
    hello = emitted.append(
        "HELLO",
        {"worker_id": context["session_id"], "protocol_version": "2.3.0", "capabilities": list(WORKER_CAPABILITIES)},
        0,
    )
    parent = RC6WorkerProtocol(context)
    parent.accept(hello)
    load = parent.append(
        "LOAD_ARTIFACT",
        {
            "artifact_hash": compiled.generated_artifact["content_hash"],
            "module_hash": compiled.generated_artifact["emitted_module_hash"],
            "entrypoint_module": compiled.generated_artifact["entrypoint"]["module"],
            "entrypoint_class": compiled.generated_artifact["entrypoint"]["class"],
        },
        0,
    )
    initialized = parent.append(
        "INIT_RUN",
        {
            "run_id": context["run_id"], "run_hash": "sha256:" + "1" * 64,
            "execution_context_hash": context["content_hash"], "execution_context": context,
            "semantic_profile": "strict_5x", "capabilities": list(WORKER_CAPABILITIES),
        },
        0,
    )
    return request, [load, initialized], context


def _run_bulk_with_lines(monkeypatch: pytest.MonkeyPatch, request: dict[str, object], context: dict[str, object], lines: list[dict[str, object]]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(line) + "\n" for line in lines)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    run_bulk(request, RC6WorkerProtocol(context))


def test_bulk_worker_rejects_parent_tape_before_engine_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    request, handshake, context = _bulk_request_and_frames()

    with pytest.raises(ValueError, match="initialized before bulk bars"):
        _run_bulk_with_lines(monkeypatch, request, context, [{"kind": "BULK_BARS", "bars": [], "last": True}])
    with pytest.raises(ValueError, match="bulk bars payload is invalid"):
        _run_bulk_with_lines(monkeypatch, request, context, [*handshake, {"kind": "BULK_BARS", "bars": {}, "last": True}])
    with pytest.raises(ValueError, match="ended before its final batch"):
        _run_bulk_with_lines(monkeypatch, request, context, handshake)
    with pytest.raises(ValueError, match="did not receive bars"):
        _run_bulk_with_lines(monkeypatch, request, context, [*handshake, {"kind": "BULK_BARS", "bars": [], "last": True}])

    wrong_load = {**handshake[0], "body": {**handshake[0]["body"], "entrypoint_class": "Wrong"}}
    wrong_load = seal_content_hash(wrong_load, schema_id="openpine.worker.protocol.v2")
    with pytest.raises(ValueError, match="loaded artifact identity mismatch"):
        _run_bulk_with_lines(monkeypatch, request, context, [wrong_load])


def test_executor_artifact_context_rejects_all_unbound_or_tampered_forms() -> None:
    valid_context = execution_context()
    envelope = {"schema_id": "openpine.generated_artifact.v3", "content_hash": HASH_B,
        "source_hash": HASH_C, "emitted_module_hash": STACK_HASH}
    with pytest.raises(RuntimeError, match="sealed V3"):
        executor_module._execution_context_from_artifact({"generated_artifact": {}})
    with pytest.raises(RuntimeError, match="execution_context"):
        executor_module._execution_context_from_artifact({"generated_artifact": envelope})

    invalid_context = {**valid_context, "run_id": 1}
    with pytest.raises(RuntimeError, match="execution_context.v1 is invalid"):
        executor_module._execution_context_from_artifact({"generated_artifact": envelope, "execution_context": invalid_context})
    unsealed_context = {**valid_context, "content_hash": "sha256:" + "e" * 64}
    with pytest.raises(RuntimeError, match="content hash is invalid"):
        executor_module._execution_context_from_artifact({"generated_artifact": envelope, "execution_context": unsealed_context})
    mismatched_artifact = {**envelope, "content_hash": HASH_C}
    with pytest.raises(RuntimeError, match="does not bind"):
        executor_module._execution_context_from_artifact({"generated_artifact": mismatched_artifact, "execution_context": valid_context})
    mismatched_emitted = {**envelope, "emitted_module_hash": HASH_B}
    with pytest.raises(RuntimeError, match="emitted_module_hash"):
        executor_module._execution_context_from_artifact({"generated_artifact": mismatched_emitted, "execution_context": valid_context})


def _strategy() -> StrategyInstance:
    return StrategyInstance(
        strategy_id="strategy-contract",
        name="contract",
        pine_id="pine-contract",
        artifact_id="artifact-contract",
        params_json="{}",
        params_hash="params-contract",
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode="live",
        enabled=True,
        status="running",
    )


def _bar() -> Bar:
    timeframe = parse_timeframe("15m")
    return Bar(
        InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe,
        900_000,
        1_800_000,
        100.0,
        110.0,
        90.0,
        105.0,
        1.0,
        True,
    )


def test_executor_publishes_live_position_and_filters_out_of_bar_trades_to_sqlite(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "ledger.sqlite")
    MigrationRunner().run_migrations(storage)
    try:
        ledger = StrategyLedger(storage)
        strategy = _strategy()
        executor = StrategyJobExecutor(
            registry=SimpleNamespace(), orchestrator=SimpleNamespace(), scheduler=SimpleNamespace(),
            state_store=SimpleNamespace(), ledger=ledger,
        )
        executor._job_broker_account_ref = "live-account"
        bar = _bar()
        before_bar = SimpleNamespace(id="old", entry_id="old", exit_id="old-x", direction="long", entry_time=1,
            exit_time=bar.time - 1, entry_price=1, exit_price=2, qty=1, profit=1, commission_entry=0, commission_exit=0)
        committed = SimpleNamespace(id="new", entry_id="short", exit_id="close", direction="short", entry_time=bar.time,
            exit_time=bar.time_close, entry_price=104, exit_price=102, qty=3, profit=6, commission_entry=0.5, commission_exit=0.5)
        raw = SimpleNamespace(
            open_trades=[
                SimpleNamespace(direction="short", qty=1, entry_price=100),
                SimpleNamespace(direction="short", qty=3, entry_price=105),
            ],
            closed_trades=[before_bar, committed], net_profit=7,
        )
        recorded = executor._record_ledger(
            strategy,
            Job(JobType.LIVE_BAR_PROCESS, strategy_id=strategy.strategy_id),
            bar,
            SimpleNamespace(raw_result=raw, resume_state=None),
        )

        position = ledger.get_position(
            strategy_id=strategy.strategy_id, account_id="live-account", exchange="binance",
            market_type="spot", symbol="BTCUSDT", timeframe="15m",
        )
        trades = ledger.list_trades(strategy_id=strategy.strategy_id)
        assert recorded == 1
        assert position is not None
        assert position.side == PositionSide.SHORT and position.qty == 4
        assert position.avg_price == 103.75 and position.realized_pnl == 7
        assert [(trade.entry_id, trade.source, trade.fee) for trade in trades] == [
            ("short", LedgerSource.LIVE, 1.0)
        ]
    finally:
        storage.close()


def test_executor_helpers_fail_closed_for_invalid_artifacts_and_paper_identity() -> None:
    strategy = _strategy()
    assert executor_module._strategy_job_type("live") is JobType.LIVE_BAR_PROCESS
    assert executor_module._strategy_job_type("observe") is JobType.OBSERVE_BAR_PROCESS
    assert executor_module._strategy_job_type("paper") is JobType.PAPER_BAR_PROCESS
    with pytest.raises(RuntimeError, match="unsupported stored"):
        executor_module._strategy_job_type("unsupported")

    with pytest.raises(RuntimeError, match="backtest-engine"):
        executor_module._paper_broker_identity(strategy, SimpleNamespace(wheel_identities=()), paper_epoch_start=0)
    with pytest.raises(RuntimeError, match="wheel identity is invalid"):
        executor_module._paper_broker_identity(
            strategy,
            SimpleNamespace(wheel_identities=(("backtest-engine", "", "sha256:" + "a" * 64),)),
            paper_epoch_start=0,
        )
    strategy.params_hash = ""
    with pytest.raises(RuntimeError, match="account identity is incomplete"):
        executor_module._paper_broker_identity(
            strategy,
            SimpleNamespace(wheel_identities=(("backtest-engine", "5", "sha256:" + "a" * 64),)),
            paper_epoch_start=0,
        )

    assert executor_module._load_sealed_artifact(StrategyInstance("s", "n", "", "", "{}", "h", "BTC", "1m")) is None
    assert executor_module._load_sealed_artifact(_strategy(), artifact_store=SimpleNamespace(get_artifact=lambda *_: "not-a-dict")) is None
    assert executor_module._execution_context_from_artifact({}) is None
    artifact = {
        "generated_artifact": {"schema_id": "openpine.generated_artifact.v3", "content_hash": HASH_B,
            "source_hash": HASH_C, "emitted_module_hash": STACK_HASH},
        "execution_context": execution_context(),
    }
    assert executor_module._execution_context_from_artifact(artifact) == artifact["execution_context"]
    artifact["generated_artifact"] = {**artifact["generated_artifact"], "source_hash": HASH_B}
    with pytest.raises(RuntimeError, match="source_hash"):
        executor_module._execution_context_from_artifact(artifact)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
        ({"backoff_initial_seconds": -1}, "restart backoff"),
        ({"max_restarts": -1}, "max_restarts"),
        ({"restart_window_seconds": 0}, "restart_window"),
        ({"shutdown_timeout_seconds": -1}, "shutdown timeouts"),
        ({"heartbeat_stale_seconds": 0}, "heartbeat_stale"),
        ({"startup_readiness_timeout_seconds": 0}, "startup_readiness"),
        ({"fail_safe_max_attempts": 0}, "fail-safe retry"),
        ({"fail_safe_attempt_timeout_seconds": 0}, "fail_safe_attempt_timeout"),
    ],
)
def test_supervisor_config_rejects_invalid_health_and_shutdown_bounds(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        SupervisorConfig(**kwargs)


def test_legacy_worker_snapshot_reports_unknown_liveness_without_touching_storage() -> None:
    class BrokenProcess:
        pid = 42
        exitcode = 17

        def is_alive(self) -> bool:
            raise RuntimeError("pipe closed")

    snapshot = worker_runtime_snapshot(SimpleNamespace(_background_worker_process=BrokenProcess()))
    assert snapshot["enabled"] is True
    assert snapshot["liveness"] == "unknown"
    assert snapshot["degraded"] is True
    assert snapshot["reason"] == "legacy_unsupervised"


def test_supervisor_stop_escalates_when_process_and_stop_signal_are_unusable() -> None:
    from openpine.gateway.worker_supervisor import WorkerSupervisor

    class BrokenStopEvent:
        def set(self) -> None:
            raise RuntimeError("control pipe closed")

    class UnkillableProcess:
        pid = 71
        exitcode = None

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return True

        def join(self, _timeout: float) -> None:
            pass

        def terminate(self) -> None:
            raise RuntimeError("terminate unavailable")

        def kill(self) -> None:
            raise RuntimeError("kill unavailable")

    process = UnkillableProcess()
    supervisor = WorkerSupervisor(
        lambda: (process, BrokenStopEvent()),
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=60, shutdown_timeout_seconds=0,
            terminate_timeout_seconds=0, kill_timeout_seconds=0,
        ),
    )
    async def start_and_stop() -> bool:
        supervisor.start()
        supervisor.start()  # repeated start cannot replace a supervised process
        assert supervisor.process is process
        return await supervisor.stop()

    assert asyncio.run(start_and_stop()) is False
    status = supervisor.snapshot()
    assert status["alive"] is True
    assert status["degraded"] is True
    assert status["reason"] == "shutdown_incomplete"


def test_supervisor_fails_closed_for_bad_factories_and_process_callbacks() -> None:
    from openpine.gateway.worker_supervisor import WorkerSupervisor

    class MinimalProcess:
        pid = 72
        exitcode = 0

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

        def join(self, _timeout: float) -> None:
            pass

    def exploding_factory():
        raise RuntimeError("factory unavailable")

    def exploding_callback(_process: object) -> None:
        raise RuntimeError("registration unavailable")

    async def exercise() -> None:
        config = SupervisorConfig(poll_interval_seconds=60, max_restarts=0)
        malformed = WorkerSupervisor(
            lambda: (MinimalProcess(), object(), object()), fail_safe=lambda: None, config=config,
        )
        malformed.start()
        assert malformed.snapshot()["reason"] == "process_factory_failed"
        await malformed.stop()

        unavailable = WorkerSupervisor(exploding_factory, fail_safe=lambda: None, config=config)
        unavailable.start()
        assert unavailable.snapshot()["reason"] == "process_factory_failed"
        await unavailable.stop()

        callback_failed = WorkerSupervisor(
            lambda: (MinimalProcess(), object()), fail_safe=lambda: None, config=config,
            on_process=exploding_callback,
        )
        callback_failed.start()
        assert callback_failed.snapshot()["reason"] == "process_callback_failed"
        await callback_failed.stop()

    asyncio.run(exercise())


def test_supervisor_snapshot_fails_closed_when_signal_or_exit_status_breaks() -> None:
    from openpine.gateway.worker_supervisor import WorkerSupervisor

    class BrokenReadyEvent:
        def is_set(self) -> bool:
            raise RuntimeError("readiness descriptor closed")

    class BrokenExitProcess:
        pid = 73

        def is_alive(self) -> bool:
            return False

        @property
        def exitcode(self):
            raise RuntimeError("exit status unavailable")

    supervisor = WorkerSupervisor(lambda: (BrokenExitProcess(), object()), fail_safe=lambda: None)
    supervisor._process = BrokenExitProcess()
    supervisor._ready_event = BrokenReadyEvent()
    snapshot = supervisor.snapshot()
    assert snapshot["alive"] is False
    assert snapshot["exitcode"] is None
    assert snapshot["degraded"] is True
    assert snapshot["reason"] == "process_status_failed"
