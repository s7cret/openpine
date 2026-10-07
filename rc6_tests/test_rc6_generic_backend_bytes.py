"""Compiled foreign owner crosses bytes through BacktestEngine.run itself."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pinelib.state.checkpoint import canonical_json

from backtest_engine import BacktestCallbacks, BacktestEngine, JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from openpine.runtime.generated_backtest import RC6GeneratedExecutionBackend
from test_rc6_generated_bytes import descriptor, inputs, literal_oracle, mutate, projection, session


@pytest.fixture(scope="module")
def compiled():
    return descriptor()


def producer(data):
    config, bars = inputs()
    engine, owner, tape = BacktestEngine(config), session(data, config), []
    result = engine.run(
        owner.generated_class,
        bars=bars,
        execution_backend=RC6GeneratedExecutionBackend(owner, tape),
    )
    assert result.status == "early_stopped" and result.resume_state.bar_index == 0
    assert result.performance["execution_backend"] == "rc6-generated"
    return JsonResumeStateSerializer().dumps(result.resume_state), tape


def test_direct_public_backend_resume_matches_literal_effects_and_control(compiled):
    wire, prefix = producer(compiled)
    config, bars = inputs()
    engine, owner, tape = BacktestEngine(config), session(compiled, config), []
    result = engine.run(
        owner.generated_class,
        bars=bars,
        resume_state=wire,
        execution_backend=RC6GeneratedExecutionBackend(owner, tape),
    )
    assert result.status == "completed"
    literal_oracle(engine, tape)
    control_config = replace(config, early_stop_enabled=False)
    control, whole, all_tape = BacktestEngine(control_config), session(compiled, control_config), []
    control.run(
        whole.generated_class,
        bars=bars,
        execution_backend=RC6GeneratedExecutionBackend(whole, all_tape),
    )
    assert prefix + tape == all_tape
    assert projection(engine) == projection(control)
    assert owner.session.semantic_state_hash == whole.session.semantic_state_hash
    assert owner.execution_cursor == whole.execution_cursor


@pytest.mark.parametrize(
    "fault", ["identity", "na", "refgraph", "request", "transcript", "receipt"]
)
def test_generic_bytes_complete_owner_rejects_before_effects(compiled, fault, monkeypatch):
    wire, _ = producer(compiled)
    bad = JsonResumeStateSerializer().dumps(mutate(JsonResumeStateSerializer().loads(wire), fault))
    config, bars = inputs()
    engine, owner = BacktestEngine(config), session(compiled, config)
    engine.run(
        owner.generated_class, bars=bars, execution_backend=RC6GeneratedExecutionBackend(owner)
    )
    before, broker = owner.export_state(), projection(engine)
    identities = (
        engine.position,
        engine.orders,
        engine.fills,
        engine.callbacks,
        owner.session,
        owner.execution_cursor,
        owner.session.references,
        owner.session.requests,
        owner._callback_receipts,
    )
    calls, tape = [], []
    monkeypatch.setattr(engine, "_reset_state", lambda: pytest.fail("owner negative reached reset"))
    monkeypatch.setattr(
        owner.generated_class, "run", lambda *a, **kw: pytest.fail("negative evaluated")
    )
    monkeypatch.setattr(
        type(owner.session.requests.provider),
        "fetch",
        lambda *a, **kw: pytest.fail("negative fetched"),
    )
    with pytest.raises(ResumeUnsupportedError):
        engine.run(
            owner.generated_class,
            bars=bars,
            resume_state=bad,
            callbacks=BacktestCallbacks(on_bar_start=lambda *a: calls.append(a)),
            execution_backend=RC6GeneratedExecutionBackend(owner, tape),
        )
    assert owner.export_state() == before and projection(engine) == broker
    assert all(
        a is b
        for a, b in zip(
            identities,
            (
                engine.position,
                engine.orders,
                engine.fills,
                engine.callbacks,
                owner.session,
                owner.execution_cursor,
                owner.session.references,
                owner.session.requests,
                owner._callback_receipts,
            ),
        )
    )
    assert calls == tape == []


@pytest.mark.parametrize("mismatch", ["class", "params", "profile"])
def test_selected_owner_identity_mismatch_rejects_before_reset(compiled, mismatch, monkeypatch):
    config, bars = inputs()
    owner = session(compiled, config)
    engine = BacktestEngine(
        replace(config, semantic_profile="legacy_4x") if mismatch == "profile" else config
    )
    before = owner.export_state()
    monkeypatch.setattr(engine, "_reset_state", lambda: pytest.fail("selection mismatch reset"))
    with pytest.raises(ResumeUnsupportedError):
        engine.run(
            object if mismatch == "class" else owner.generated_class,
            params={"unapplied": 1} if mismatch == "params" else None,
            bars=bars,
            execution_backend=RC6GeneratedExecutionBackend(owner),
        )
    assert owner.export_state() == before


def process(mode, directory):
    folder = Path(directory)
    started = datetime.now(timezone.utc).isoformat()
    if mode == "producer":
        data = descriptor()
        wire, tape = producer(data)
        (folder / "descriptor.json").write_bytes(canonical_json(data))
        (folder / "checkpoint.json").write_bytes(wire)
        details = {
            "wire_bytes": len(wire),
            "prefix": tape,
            "artifact_hash": data["artifact"]["generated_artifact"]["content_hash"],
        }
    else:
        data = json.loads((folder / "descriptor.json").read_bytes())
        config, bars = inputs()
        engine, owner, tape = BacktestEngine(config), session(data, config), []
        engine.run(
            owner.generated_class,
            bars=bars,
            resume_state=(folder / "checkpoint.json").read_bytes(),
            execution_backend=RC6GeneratedExecutionBackend(owner, tape),
        )
        literal_oracle(engine, tape)
        control_config = replace(config, early_stop_enabled=False)
        control, whole = BacktestEngine(control_config), session(data, control_config)
        control.run(
            whole.generated_class, bars=bars, execution_backend=RC6GeneratedExecutionBackend(whole)
        )
        assert projection(engine) == projection(control)
        assert owner.session.semantic_state_hash == whole.session.semantic_state_hash
        assert owner.execution_cursor == whole.execution_cursor
        details = {
            "suffix": tape,
            "effects": projection(engine),
            "independent_literal_oracle": True,
            "public_control_agrees": True,
        }
    report = {
        "pid": os.getpid(),
        "pins": data["pins"],
        "passed": True,
        "public_api": "BacktestEngine.run(execution_backend=RC6GeneratedExecutionBackend, resume_state=bytes)",
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    (folder / (mode + ".json")).write_text(json.dumps(report, indent=2) + "\n")


def test_direct_public_exporter_exits_before_new_generic_consumer(tmp_path):
    outcomes = []
    for mode in ("producer", "consumer"):
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and local probe, output directory only
            [sys.executable, "-B", __file__, mode, str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outcomes.append(json.loads((tmp_path / (mode + ".json")).read_text()))
    assert outcomes[0]["pid"] != outcomes[1]["pid"]
    assert outcomes[0]["completed_utc"] < outcomes[1]["started_utc"]


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2])
