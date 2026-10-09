"""Actual compiled owners, SQLite restart, ordered delivery and generation fences."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from openpine_contracts import seal_content_hash
from pinelib.state.checkpoint import sha

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.errors import ResumeUnsupportedError
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.jobs.execution_store import JobExecutionStore
from openpine.jobs.transactional_store import JobV1Error
from openpine.run_identity import execution_context_from_admission
from openpine.runtime.generated_job import GeneratedJobExecution
from openpine.runtime.job_checkpoint import (
    decode_job_checkpoint,
    encode_job_checkpoint,
)
from openpine.runtime.rc6_config import resolve_engine_config, serialize_engine_config
from openpine.runtime.rc6_worker_runtime import _engine_bar, _session_from_request
from openpine.runtime.worker_protocol import WorkerProtocolTranscript
from rc6_tests.test_rc6_generated_bytes import head, projection
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar
from rc6_tests.test_rc6_worker_admission import _deployment, _manifest

SOURCE = """//@version=6
strategy("durable job cut")
var int ordinary = 0
varip int callbacks = 0
var array<float> prices = array.new_float(0)
ordinary += 1
callbacks += 1
array.push(prices, close)
plot(ordinary)
plot(callbacks)
if bar_index == 0
    strategy.entry("L", strategy.long, qty=3)
if bar_index == 2 and strategy.position_size == 3
    strategy.close("L", qty=1, immediately=true)
"""


def descriptor():
    from rc6_tests.source_roots import imported_source_roots
    roots = imported_source_roots()
    pins = {name: head(root) for name, root in roots.items()}
    compiled = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="durable_job",
        source_name="durable-job.pine",
        producer_commits={name: pins[name] for name in ("pine2ast", "ast2python")},
    )
    assert compiled.success, compiled.errors
    artifact = {
        "generated_artifact": compiled.generated_artifact,
        "python_code": compiled.python_code,
        "consumer_bundle": compiled.consumer_bundle,
        "source_map": compiled.source_map,
        "compile_meta": compiled.compile_meta,
    }
    manifest = _manifest()
    manifest.update(
        manifest_hash=sha(pins), components={name: {"sha": commit} for name, commit in pins.items()}
    )
    context = execution_context_from_admission(
        replace(_deployment(), stack_manifest_hash=sha(pins)),
        manifest,
        run_id="durable-run",
        strategy_id="durable-strategy",
        artifact=artifact,
        data_snapshot_hash=sha("immutable-four-bars"),
        series_id="binance:spot:SOLUSDT:1m",
        instrument_id="binance:spot:SOLUSDT",
        exchange="binance",
        market="spot",
        symbol="SOLUSDT",
        timeframe="1m",
        semantic_profile="strict_5x",
        created_at_utc_ms=0,
    )
    context = seal_content_hash(
        context | {"mintick": "1", "pointvalue": "1"}, schema_id="openpine.execution_context.v1"
    )
    return {"artifact": artifact, "context": context, "pins": pins}


@pytest.fixture(scope="module")
def compiled():
    return descriptor()


def inputs(data, *, cut=True):
    config = BacktestConfig(
        "SOLUSDT",
        "1m",
        OPENED,
        OPENED + 180000,
        mintick=1,
        initial_capital=1000,
        commission_type="none",
        commission_value=0,
        force_close_on_end=False,
        semantic_profile="strict_5x",
        export_resume_state=True,
        calc_on_order_fills=True,
        early_stop_enabled=cut,
        min_equity_stop=None,
        max_bars_without_trade=0,
    )
    config = resolve_engine_config(serialize_engine_config(config, "strict_5x"), data["context"])
    rows = [(100, 100), (100, 110), (110, 120), (120, 130)]
    envelopes = [
        bar(
            open_time_utc_ms=OPENED + i * 60000,
            close_time_utc_ms=OPENED + (i + 1) * 60000 - 1,
            stack_id=data["context"]["stack_manifest_hash"],
            producer_commit=data["pins"]["marketdata-provider"],
            open=o,
            low=o,
            high=c,
            close=c,
            volume=1,
        )
        for i, (o, c) in enumerate(rows)
    ]
    return config, [_engine_bar(row) for row in envelopes], envelopes


def owner(data, config):
    return _session_from_request(
        {
            "generated_artifact": data["artifact"]["generated_artifact"],
            "source": data["artifact"]["python_code"],
            "execution_context": data["context"],
            "engine_config": serialize_engine_config(config, "strict_5x"),
            "params": {},
        }
    )


def fresh(store, data):
    store.create(job_id="job", kind="backtest")
    store.mark_running("job", lease_owner="host", lease_deadline_utc_ms=10000, now_ms=1000)
    config, bars, envelopes = inputs(data)
    session = owner(data, config)
    generation = store.claim_execution(
        "job", context=data["context"], lease_owner="host", worker_id="worker-1", now_ms=1000
    )
    execution = GeneratedJobExecution(
        store,
        job_id="job",
        context=data["context"],
        generation=generation,
        worker_id="worker-1",
        session=session,
        generated_artifact=data["artifact"]["generated_artifact"],
        clock=lambda: 1000,
    )
    engine = BacktestEngine(config)
    result = execution.run(engine, bars, bar_envelopes=envelopes)
    assert result.status == "early_stopped" and result.resume_state.bar_index == 1
    assert [(f.side, f.qty, f.price) for f in engine.fills] == [("buy", 3.0, 100.0)]
    return execution, engine


def ack(store, generation, frame):
    return store.acknowledge(
        "job",
        "durable-run",
        generation,
        sequence=frame["sequence"],
        message_id=frame["message_id"],
        content_hash=frame["content_hash"],
        now_ms=1000,
    )


def resume(store, data, worker_id="worker-2", generation=None):
    config, bars, envelopes = inputs(data)
    session = owner(data, config)
    if generation is None:
        generation = store.claim_execution(
            "job",
            context=data["context"],
            lease_owner="host",
            worker_id=worker_id,
            now_ms=1000,
            mode="resume",
            resume_session=session,
        )
    execution = GeneratedJobExecution(
        store,
        job_id="job",
        context=data["context"],
        generation=generation,
        worker_id=worker_id,
        session=session,
        generated_artifact=data["artifact"]["generated_artifact"],
        clock=lambda: 1000,
        resume=True,
    )
    engine = BacktestEngine(config)
    result = execution.run(engine, bars, bar_envelopes=envelopes)
    return execution, engine, result


def finish(store, data, generation=None):
    before = store.frames("job", "durable-run")
    execution, engine, result = resume(store, data, generation=generation)
    assert result.resume_state.bar_index == 2
    execution, engine, result = resume(store, data, "worker-3")
    assert result.resume_state.bar_index == 3
    assert [(f.side, f.qty, f.price, f.bar_index) for f in engine.fills] == [
        ("buy", 3.0, 100.0, 1),
        ("sell", 1.0, 120.0, 2),
    ]
    assert engine.equity == 1080.0 and engine.position.realized_profit == 20.0
    assert store.frames("job", "durable-run")[: len(before)] == before
    control_data = data
    config, bars, envelopes = inputs(control_data, cut=False)
    session = owner(control_data, config)
    from openpine.runtime.generated_backtest import RC6GeneratedExecutionBackend

    control = BacktestEngine(config)
    control.run(
        session.generated_class,
        bars=bars,
        execution_backend=RC6GeneratedExecutionBackend(session),
        execution_context=data["context"],
        bar_envelopes=envelopes,
    )
    assert projection(engine) == projection(control)
    assert execution.session.session.semantic_state_hash == session.session.semantic_state_hash
    assert execution.session.execution_cursor == session.execution_cursor
    return execution, engine


def test_job_restart_retains_ack_prefix_and_continues_actual_broker_pine(compiled, tmp_path):
    path = tmp_path / "jobs.sqlite"
    with JobExecutionStore(path) as store:
        execution, _ = fresh(store, compiled)
        for frame in store.frames("job", "durable-run")[:5]:
            assert ack(store, 1, frame)
        assert not ack(store, 1, store.frames("job", "durable-run")[0])
    with JobExecutionStore(path) as store:
        execution, engine = finish(store, compiled)
        checkpoint = decode_job_checkpoint(store.checkpoint("job", "durable-run"))
        assert checkpoint["worker_generation"] == 3 and checkpoint["last_acknowledged_frame"] == 4
        assert store.pending_frames("job", "durable-run", 3, now_ms=1000)[0]["sequence"] == 5
        assert engine.equity == 1080


@pytest.mark.parametrize(
    "mutation", ["ack-bool", "callback", "native", "artifact", "late-protocol"]
)
def test_late_corruption_retains_checkpoint_generation_and_receiver(compiled, tmp_path, mutation):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        execution, engine = fresh(store, compiled)
        good = store.checkpoint("job", "durable-run")
        payload = decode_job_checkpoint(good)
        if mutation == "ack-bool":
            payload["last_acknowledged_frame"] = True
        elif mutation == "callback":
            payload["callback_sequence"] += 1
        elif mutation == "native":
            payload["native_checkpoint"] = "eyJicm9rZXIiOiJwbGFpbiBkaWN0In0="
        elif mutation == "artifact":
            payload["artifacts"][next(reversed(payload["artifacts"]))] = "bnVsbA=="
        else:
            payload["protocol_messages"][-1]["body"]["bar_index"] += 1
            payload["protocol_messages"][-1] = seal_content_hash(
                payload["protocol_messages"][-1], schema_id="openpine.worker.protocol.v2"
            )
        before = execution.session.export_state(), projection(engine)
        with pytest.raises((ValueError, ResumeUnsupportedError)):
            store.publish_checkpoint(
                "job",
                "durable-run",
                1,
                encode_job_checkpoint(payload),
                context=compiled["context"],
                session=execution.session,
                now_ms=1000,
            )
        assert store.checkpoint("job", "durable-run") == good
        assert (execution.session.export_state(), projection(engine)) == before


def test_ack_gap_bad_identity_and_stale_worker_are_rejected(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        frames = store.frames("job", "durable-run")
        with pytest.raises(JobV1Error, match="gap"):
            ack(store, 1, frames[1])
        with pytest.raises(JobV1Error, match="identity"):
            ack(store, 1, frames[0] | {"content_hash": sha("other")})
        resume(store, compiled)
        for action in (
            lambda: ack(store, 1, frames[0]),
            lambda: store.record_frame("job", "durable-run", 1, frames[0], now_ms=1000),
            lambda: store.pending_frames("job", "durable-run", 1, now_ms=1000),
        ):
            with pytest.raises(JobV1Error, match="stale"):
                action()
        assert ack(store, 2, frames[0])


def test_transport_repeat_is_exact_and_provisional_frames_stay_private(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        frames = store.frames("job", "durable-run")
        assert not store.record_frame("job", "durable-run", 1, frames[0], now_ms=1000)
        conflict = frames[0] | {"created_at_utc_ms": 1}
        conflict = seal_content_hash(conflict, schema_id="openpine.worker.protocol.v2")
        with pytest.raises(JobV1Error, match="conflicting"):
            store.record_frame("job", "durable-run", 1, conflict, now_ms=1000)
        config, bars, envelopes = inputs(compiled)
        session = owner(compiled, config)
        generation = store.claim_execution(
            "job",
            context=compiled["context"],
            lease_owner="host",
            worker_id="worker-2",
            now_ms=1000,
            mode="resume",
            resume_session=session,
        )
        execution = GeneratedJobExecution(
            store,
            job_id="job",
            context=compiled["context"],
            generation=generation,
            worker_id="worker-2",
            session=session,
            generated_artifact=compiled["artifact"]["generated_artifact"],
            clock=lambda: 1000,
            resume=True,
        )

        def fail_commit(*args, **kwargs):
            raise OSError("crash before checkpoint publication")

        store.publish_checkpoint = fail_commit
        with pytest.raises(OSError, match="crash"):
            execution.run(BacktestEngine(config), bars, bar_envelopes=envelopes)
        assert store.frames("job", "durable-run") == frames
        assert len(store.frames("job", "durable-run", committed_only=False)) > len(frames)
        with pytest.raises(JobV1Error, match="provisional"):
            ack(store, 2, store.frames("job", "durable-run", committed_only=False)[-1])
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        finish(store, compiled)


def test_public_job_bytes_have_no_producer_process_dependencies(compiled, tmp_path):
    reports = []
    for mode in ("producer", "consumer"):
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and local process proof
            [sys.executable, "-B", __file__, mode, str(tmp_path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr
        reports.append(json.loads((tmp_path / (mode + ".json")).read_text()))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["completed_utc"] < reports[1]["started_utc"]
    assert reports[0]["pins"] == reports[1]["pins"]


def test_checkpoint_and_event_write_rollback_together(compiled, tmp_path, monkeypatch):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        good = store.checkpoint("job", "durable-run")
        original = store._append_event_in_transaction

        def fail_publication(job_id, kind, *args, **kwargs):
            if kind == "execution_checkpoint":
                raise OSError("failed durable publication")
            return original(job_id, kind, *args, **kwargs)

        monkeypatch.setattr(store, "_append_event_in_transaction", fail_publication)
        with pytest.raises(OSError, match="durable publication"):
            resume(store, compiled)
        after = decode_job_checkpoint(store.checkpoint("job", "durable-run"))
        before = decode_job_checkpoint(good)
        assert after["worker_generation"] == 2
        for field in (
            "native_checkpoint",
            "committed_sequence",
            "protocol_messages",
            "last_acknowledged_frame",
        ):
            assert after[field] == before[field]
        assert len(store.frames("job", "durable-run", committed_only=False)) > len(
            store.frames("job", "durable-run")
        )


def test_job_retry_keeps_run_new_run_starts_new_sequences_and_fences_old_run(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        store.recover_worker_leases(active_lease_owner="new-host", now_ms=1000)
        store.retry("job")
        store.mark_running("job", lease_owner="new-host", lease_deadline_utc_ms=10000, now_ms=1000)
        config, _, _ = inputs(compiled)
        generation = store.claim_execution(
            "job",
            context=compiled["context"],
            lease_owner="new-host",
            worker_id="worker-2",
            now_ms=1000,
            mode="resume",
            resume_session=owner(compiled, config),
        )
        assert generation == 2
        assert (
            store.claim_execution(
                "job",
                context=compiled["context"],
                lease_owner="new-host",
                worker_id="worker-2",
                now_ms=1000,
                mode="resume",
                resume_session=owner(compiled, config),
            )
            == 2
        )
        old_frames = store.frames("job", "durable-run")
        context = seal_content_hash(
            compiled["context"] | {"run_id": "different-run", "session_id": "new-session"},
            schema_id="openpine.execution_context.v1",
        )
        with pytest.raises(JobV1Error, match="new_run"):
            store.claim_execution(
                "job", context=context, lease_owner="new-host", worker_id="worker-3", now_ms=1000
            )
        assert (
            store.claim_execution(
                "job",
                context=context,
                lease_owner="new-host",
                worker_id="worker-3",
                now_ms=1000,
                mode="new_run",
            )
            == 3
        )
        assert store.frames("job", "different-run") == []
        with pytest.raises(JobV1Error, match="active RUNNING run"):
            store.pending_frames("job", "durable-run", 2, now_ms=1000)
        assert store.frames("job", "durable-run") == old_frames


@pytest.mark.parametrize("mutation", ["data", "worker-abi", "library", "expired-lease"])
def test_replacement_refuses_incompatible_context_or_expired_lease(compiled, tmp_path, mutation):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        good = store.checkpoint("job", "durable-run")
        context = json.loads(json.dumps(compiled["context"]))
        if mutation == "data":
            context["data_snapshot_hash"] = sha("different-data")
        elif mutation == "worker-abi":
            context["schema_hashes"]["openpine.worker.protocol.v2"] = sha("different-abi")
        elif mutation == "library":
            context["producer_commits"]["pinelib"] = "a" * 40
        context = seal_content_hash(context, schema_id="openpine.execution_context.v1")
        config, _, _ = inputs(compiled)
        session = owner(compiled, config)
        before = session.export_state()
        with pytest.raises((ValueError, ResumeUnsupportedError)):
            store.claim_execution(
                "job",
                context=context,
                lease_owner="host",
                worker_id="worker-2",
                now_ms=10001 if mutation == "expired-lease" else 1000,
                mode="resume",
                resume_session=session,
            )
        assert store.checkpoint("job", "durable-run") == good and session.export_state() == before


@pytest.mark.parametrize(
    "raw", [b'{"schema":1,"schema":2}', b'{"x":NaN}', b'{"x":1e999}', b"[" * 65]
)
def test_job_envelope_reuses_existing_duplicate_numeric_and_depth_guards(raw):
    with pytest.raises((ValueError, ResumeUnsupportedError)):
        decode_job_checkpoint(raw)


def test_durable_sink_failure_does_not_advance_protocol_cursor(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        message = store.frames("job", "durable-run")[0]

        def unavailable(_message):
            raise OSError("durable sink unavailable")

        protocol = WorkerProtocolTranscript(compiled["context"], on_message=unavailable)
        with pytest.raises(OSError, match="sink unavailable"):
            protocol.accept(message)
        assert protocol.next_sequence == 0 and protocol.last_message_id is None
        assert protocol.messages == ()


def test_portable_import_cannot_roll_back_existing_ack_ledger(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        wire = store.checkpoint("job", "durable-run")
        ack(store, 1, store.frames("job", "durable-run")[0])
        config, _, _ = inputs(compiled)
        with pytest.raises(JobV1Error, match="new RUNNING execution ledger"):
            store.import_checkpoint(
                "job",
                wire,
                context=compiled["context"],
                session=owner(compiled, config),
                lease_owner="host",
                worker_id="new-worker",
                now_ms=1000,
            )
        assert (
            decode_job_checkpoint(store.checkpoint("job", "durable-run"))["last_acknowledged_frame"]
            == 0
        )


def test_swallowed_callback_failure_cannot_publish_a_later_cut(compiled, tmp_path, monkeypatch):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        good = store.checkpoint("job", "durable-run")
        config, bars, envelopes = inputs(compiled)
        session = owner(compiled, config)
        generation = store.claim_execution(
            "job",
            context=compiled["context"],
            lease_owner="host",
            worker_id="worker-2",
            now_ms=1000,
            mode="resume",
            resume_session=session,
        )
        execution = GeneratedJobExecution(
            store,
            job_id="job",
            context=compiled["context"],
            generation=generation,
            worker_id="worker-2",
            session=session,
            generated_artifact=compiled["artifact"]["generated_artifact"],
            clock=lambda: 1000,
            resume=True,
        )

        def unavailable(_event):
            raise OSError("artifact fsync unavailable")

        monkeypatch.setattr(execution, "_process_event", unavailable)
        with pytest.raises((OSError, ResumeUnsupportedError), match="unavailable|committed cut"):
            execution.run(BacktestEngine(config), bars, bar_envelopes=envelopes)
        current = decode_job_checkpoint(store.checkpoint("job", "durable-run"))
        before = decode_job_checkpoint(good)
        assert current["native_checkpoint"] == before["native_checkpoint"]
        assert current["committed_sequence"] == before["committed_sequence"]


def process(mode, directory):
    folder = Path(directory)
    started = datetime.now(timezone.utc).isoformat()
    if mode == "producer":
        data = descriptor()
        (folder / "descriptor.json").write_text(json.dumps(data) + "\n")
        with JobExecutionStore(folder / "jobs.sqlite") as store:
            fresh(store, data)
            frames = store.frames("job", "durable-run")
            for frame in frames[:5]:
                ack(store, 1, frame)
            wire = store.checkpoint("job", "durable-run")
            (folder / "checkpoint.json").write_bytes(wire)
        details = {
            "wire_bytes": len(wire),
            "wire_sha256": hashlib.sha256(wire).hexdigest(),
            "last_ack": 4,
            "generation": 1,
        }
    else:
        data = json.loads((folder / "descriptor.json").read_text())
        with JobExecutionStore(folder / "consumer.sqlite") as store:
            store.create(job_id="job", kind="backtest")
            store.mark_running("job", lease_owner="host", lease_deadline_utc_ms=10000, now_ms=1000)
            config, _, _ = inputs(data)
            generation = store.import_checkpoint(
                "job",
                (folder / "checkpoint.json").read_bytes(),
                context=data["context"],
                session=owner(data, config),
                lease_owner="host",
                worker_id="worker-2",
                now_ms=1000,
            )
            assert generation == 2
            execution, engine = finish(store, data, generation=generation)
            state = decode_job_checkpoint(store.checkpoint("job", "durable-run"))
            assert state["last_acknowledged_frame"] == 4 and state["worker_generation"] == 3
            details = {
                "effects": projection(engine),
                "last_ack": 4,
                "generation": 3,
                "semantic_hash": execution.session.session.semantic_state_hash,
            }
    report = {
        "pid": os.getpid(),
        "pins": data["pins"],
        "passed": True,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    (folder / (mode + ".json")).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    process(*sys.argv[1:])
