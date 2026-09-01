from __future__ import annotations

import asyncio
import inspect
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import backtest_engine
import openpine.artifacts as artifacts_module
import openpine_contracts
from openpine.gateway.routes import trading
from openpine.gateway.schemas import PaperStartRequest
from openpine.runtime import isolated_run, isolated_worker, rc6_worker_runtime


def test_p0_001_worker_messages_use_packaged_protocol_not_legacy_message_types() -> None:
    child = inspect.getsource(rc6_worker_runtime)
    parent = inspect.getsource(isolated_worker.InteractiveWorkerSession)
    violations: list[str] = []

    if "openpine.worker.protocol.v2" not in openpine_contracts.list_schema_ids():
        violations.append("packaged openpine.worker.protocol.v2 schema is missing")
    for side, source in (("child", child), ("parent", parent)):
        if "validate_payload" not in source:
            violations.append(f"{side} does not validate protocol payloads")
        if "message_type" in source:
            violations.append(f"{side} still uses legacy message_type")
    if '"schema_id": "openpine.worker.protocol.v2"' not in child:
        violations.append("child messages are not full packaged protocol envelopes")
    if 'if message.get("schema_id")' in child:
        violations.append("child validates protocol messages only conditionally")
    if 'if payload.get("schema_id")' in parent:
        violations.append("parent validates protocol messages only conditionally")
    for legacy in ('"PING"', '"PONG"'):
        if legacy in child or legacy in parent:
            violations.append(f"production protocol still exposes legacy {legacy}")
    for message in ("HELLO", "BAR_BEGIN", "INTENT_BATCH"):
        if message not in child + parent:
            violations.append(f"actual {message} path is missing")

    assert not violations, "; ".join(violations)


def test_p0_003_interactive_session_requires_sealed_execution_context_identity() -> None:
    signature = inspect.signature(isolated_worker.InteractiveWorkerSession)
    params = signature.parameters
    source = inspect.getsource(isolated_worker.InteractiveWorkerSession)
    violations: list[str] = []
    context = params.get("execution_context")

    if context is None or context.default is not inspect.Parameter.empty:
        violations.append("required execution_context is absent")
    elif "ExecutionContext" not in str(context.annotation):
        violations.append("execution_context is not typed as sealed ExecutionContext")
    if "stack_id" in params:
        violations.append("caller-controlled stack_id remains accepted")
    if "stack_manifest_hash" not in source:
        violations.append("exact admitted manifest identity is not checked")

    assert not violations, "; ".join(violations)


def test_p0_004_worker_rejects_missing_instrument_identity_before_any_bar() -> None:
    signature = inspect.signature(isolated_worker.InteractiveWorkerSession)
    violations: list[str] = []
    try:
        signature.bind(
            b"class GeneratedStrategy: pass\n",
            semantic_profile="strict_5x",
            chart_timeframe="1m",
        )
    except TypeError:
        pass
    else:
        violations.append("session accepts launch without instrument identity")
    if 'SymbolInfo(tickerid="S")' in isolated_worker._BOOTSTRAP:
        violations.append('child still invents tickerid "S"')

    assert not violations, "; ".join(violations)


def test_p0_005_resume_fails_before_worker_when_checkpoint_protocol_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[bool] = []

    class UnsafeLaunch:
        def __init__(self, *args, **kwargs) -> None:
            launched.append(True)
            raise AssertionError("worker launched before resume admission")

    monkeypatch.setattr(isolated_run, "InteractiveWorkerSession", UnsafeLaunch)
    config = SimpleNamespace(semantic_profile="strict_5x", timeframe="1m")

    with pytest.raises(
        isolated_run.IsolatedRunError,
        match="RESUME_UNSUPPORTED_FOR_WORKER_PROTOCOL",
    ):
        isolated_run.run_isolated_artifact(
            b"class GeneratedStrategy: pass\n",
            bars=[],
            config=config,
            resume_state={"current_dummy_restore_must_not_be_accepted": True},
        )
    assert not launched


def test_p0_006_engine_canonical_projection_is_forwarded_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_projection = {"schema_id": "engine.canonical.projection", "opaque": object()}
    seen: dict[str, object] = {}

    class Callbacks:
        def __init__(self, *, on_protocol_callback) -> None:
            self.on_protocol_callback = on_protocol_callback

    class Engine:
        def __init__(self, config) -> None:
            self.config = config

        def run(self, strategy_class, *, bars, callbacks, resume_state=None, **kwargs):
            callbacks.on_protocol_callback(
                {
                    "kind": "BAR_BEGIN",
                    "run_id": "run",
                    "bar_index": 0,
                    "bar_open_time_utc_ms": 1,
                    "recalc_iteration": 0,
                    "bar_hash": "sha256:" + "a" * 64,
                    "bar": {},
                    "broker_projection": canonical_projection,
                }
            )
            strategy_class({}, None, SimpleNamespace()).run_bar(bars[0], 0)
            return SimpleNamespace(score_ledger_hash="hash")

    class Session:
        hello = {"isolation": {}}

        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            pass

        def evaluate_bar(self, event):
            seen["projection"] = event["broker_projection"]
            return {"intents": [{"kind": "test"}]}

        def commit_bar(self, event):
            raise AssertionError("unexpected BAR_COMMIT in focused forwarding probe")

    def local_projection_must_not_run(_payload):
        raise AssertionError("local _strategy_projection remains production authority")

    monkeypatch.setattr(backtest_engine, "BacktestCallbacks", Callbacks)
    monkeypatch.setattr(backtest_engine, "BacktestEngine", Engine)
    monkeypatch.setattr(isolated_run, "InteractiveWorkerSession", Session)
    monkeypatch.setattr(isolated_run, "_strategy_projection", local_projection_must_not_run)
    monkeypatch.setattr(
        isolated_run,
        "require_live_tape",
        lambda events: SimpleNamespace(events=tuple(events), identity="identity"),
    )
    monkeypatch.setattr(
        isolated_run,
        "ValidatedIntentTape",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(isolated_run, "apply_live_intents_for_bar", lambda *args, **kwargs: None)

    bar = SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=0)
    isolated_run.run_isolated_artifact(
        b"unused",
        bars=[bar],
        config=SimpleNamespace(semantic_profile="strict_5x", timeframe="1m"),
    )

    assert seen["projection"] is canonical_projection


def test_p0_008_paper_start_requires_router_before_running_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutations: list[tuple[str, str, str]] = []
    strategy = SimpleNamespace(strategy_id="s1", status="paused", archived=False)

    class Registry:
        def get_strategy(self, strategy_id: str):
            assert strategy_id == "s1"
            return strategy

        def activate_strategy(self, strategy_id: str, *, status: str, mode: str) -> None:
            mutations.append((strategy_id, status, mode))

    monkeypatch.setattr(trading, "require_http_admit", lambda state, mode: None)
    monkeypatch.setattr(
        trading,
        "_require_semantic_profile",
        lambda **kwargs: SimpleNamespace(value="strict_5x"),
    )
    monkeypatch.setattr(trading, "_stamp_strategy_profile", lambda *args, **kwargs: None)
    monkeypatch.setattr(trading, "_stamp_strategy_mtf", lambda *args, **kwargs: None)
    monkeypatch.setattr(trading, "guarded_strategy_activation", lambda state: nullcontext())
    state = SimpleNamespace(strategy_registry=Registry(), execution_router=None)

    with pytest.raises(HTTPException, match="CANONICAL_EXECUTION_ROUTER_REQUIRED"):
        asyncio.run(
            trading.start_paper(
                PaperStartRequest(strategy_id="s1", semantic_profile="strict_5x"),
                state,
            )
        )
    assert mutations == []


def test_p0_009_execution_loader_rejects_python_without_generated_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LegacyStore:
        def get_artifact(self, artifact_id: str, source_id: str):
            return {
                "artifact_id": artifact_id,
                "source_id": source_id,
                "python_code": "class GeneratedStrategy: pass\n",
                "compile_meta": {"compile_status": "OK"},
            }

    monkeypatch.setattr(artifacts_module, "ArtifactStore", LegacyStore)

    with pytest.raises(
        isolated_run.IsolatedRunError,
        match="GENERATED_ARTIFACT_ENVELOPE_REQUIRED",
    ):
        isolated_run.capture_generated_source("source", "artifact")


def test_p0_012_spawn_policy_is_derived_only_from_sealed_manifest() -> None:
    signature = inspect.signature(isolated_worker._bwrap_argv)
    source = inspect.getsource(isolated_worker._bwrap_argv)
    violations: list[str] = []

    if not ({"execution_context", "admitted_manifest"} & set(signature.parameters)):
        violations.append("spawn builder has no sealed admitted-manifest input")
    for hardcoded in (
        "BWRAP",
        "SANDBOX_PYTHON",
        "WORKER_USER",
        "TMPFS_BYTES",
        "_stage_trusted_packages",
        "MemoryMax=134217728",
        "TasksMax=32",
    ):
        if hardcoded in source:
            violations.append(f"spawn policy still depends on hardcoded {hardcoded}")

    assert not violations, "; ".join(violations)
