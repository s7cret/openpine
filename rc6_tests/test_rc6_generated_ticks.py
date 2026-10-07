"""Actual compiled Pine ticks cross committed bytes through the public engine."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pinelib.state.checkpoint import canonical_json

from backtest_engine import BacktestCallbacks, BacktestEngine, JsonResumeStateSerializer, Tick
from backtest_engine.errors import ResumeUnsupportedError
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.generated_backtest import RC6GeneratedExecutionBackend
from test_rc6_generated_bytes import descriptor, inputs, mutate, projection, session

SOURCE = """//@version=6
strategy("compiled ticks")
var int ordinary = 0
varip int callbacks = 0
var float missing = na
var array<float> prices = array.new_float(0)
ordinary += 1
callbacks += 1
array.push(prices, close)
if barstate.isconfirmed and bar_index == 0 and na(missing) and ordinary == 1
    strategy.entry("tick-entry", strategy.long, qty=3)
if barstate.isconfirmed and ordinary == 3 and array.size(prices) == 3 and close == 120 and strategy.position_size == 3
    strategy.close("tick-entry", qty=1, immediately=true)
plot(ordinary)
plot(callbacks)
"""


def tick_descriptor():
    data = descriptor()
    compiled = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="run02_generated_ticks",
        source_name="generated-ticks.pine",
        producer_commits={name: data["pins"][name] for name in ("pine2ast", "ast2python")},
    )
    assert compiled.success, compiled.errors
    data["artifact"] = {
        "generated_artifact": compiled.generated_artifact,
        "python_code": compiled.python_code,
    }
    return data


def scenario(*, cut=True):
    config, bars = inputs()
    ticks = []
    for bar in bars:
        prices = (bar.open, (bar.open + bar.close) / 2, bar.close)
        ticks.extend(
            Tick(bar.time + index * 10000, price, volume=volume)
            for index, (price, volume) in enumerate(zip(prices, (0.25, 0.25, 0.5), strict=True))
        )
    return replace(
        config,
        calc_on_every_tick=True,
        calc_on_order_fills=True,
        experimental_intrabar_strategy_mode=True,
        realtime_ticks=ticks,
        early_stop_enabled=cut,
    ), bars


@pytest.fixture(scope="module")
def compiled():
    return tick_descriptor()


def run(data, *, wire=None, cut=True, callbacks=None, changes=None):
    config, bars = scenario(cut=cut)
    config = replace(config, **(changes or {}))
    engine, owner, tape = BacktestEngine(config), session(data, config), []
    result = engine.run(
        owner.generated_class,
        bars=bars,
        resume_state=wire,
        callbacks=callbacks,
        execution_backend=RC6GeneratedExecutionBackend(owner, tape),
    )
    return engine, owner, tape, result


def producer(data):
    engine, owner, tape, result = run(data)
    assert result.status == "early_stopped" and result.resume_state.bar_index == 0
    assert owner.execution_cursor.open_bar is None
    assert result.resume_state.runtime_state is None
    assert result.resume_state.metadata["realtime_runtime_owner"] == "strategy-checkpoint-v1"
    return JsonResumeStateSerializer().dumps(result.resume_state), tape


def literal_oracle(engine, owner):
    assert [(f.side, f.qty, f.price, f.bar_index) for f in engine.fills] == [
        ("buy", 3.0, 100.0, 1),
        ("sell", 1.0, 120.0, 2),
    ]
    assert (engine.position.direction, engine.position.size, engine.position.avg_price) == (
        "long",
        2.0,
        100.0,
    )
    assert engine.position.realized_profit == 20.0 and engine.equity == 1080.0
    assert owner.execution_cursor.open_bar is None
    runtime = owner.export_state()["runtime"]["state"]
    assert [event["payload"]["series"] for event in runtime["visuals"]["committed"]] == [
        1,
        3,
        2,
        7,
        3,
        11,
        4,
        14,
    ]
    scalars = [row for row in runtime["slots"] if row["owner"] == "ast2python.scalar.v1"]
    assert [row["committed"] for row in scalars if row["varip"]] == [14]
    assert 4 in [row["committed"] for row in scalars if not row["varip"]]


def test_compiled_tick_public_bytes_continue_matches_control_and_literal_effects(compiled):
    wire, prefix = producer(compiled)
    engine, owner, suffix, result = run(compiled, wire=wire)
    assert result.status == "completed"
    literal_oracle(engine, owner)
    control, whole, tape, control_result = run(compiled, cut=False)
    assert prefix + suffix == tape
    assert projection(engine) == projection(control)
    assert owner.session.semantic_state_hash == whole.session.semantic_state_hash
    assert owner.execution_cursor == whole.execution_cursor
    control_broker = control_result.resume_state.broker_state
    # The independent control disables only the producer's intentional cut.
    assert result.resume_state.broker_state == replace(
        control_broker,
        risk_state=control_broker.risk_state | {"_early_stop_enabled": True},
    )
    assert result.resume_state.statistics_state == control_result.resume_state.statistics_state
    assert owner.export_state() == whole.export_state() | {
        "identity": owner.export_state()["identity"],
        "content_hash": owner.export_state()["content_hash"],
    }


@pytest.mark.parametrize("fault", ["identity", "na", "refgraph", "transcript", "receipt"])
def test_compiled_tick_complete_graph_rejects_before_live_reset(compiled, fault, monkeypatch):
    wire, _ = producer(compiled)
    state = mutate(JsonResumeStateSerializer().loads(wire), fault)
    bad = JsonResumeStateSerializer().dumps(state)
    engine, owner, _, _ = run(compiled)
    config, bars = scenario()
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
    monkeypatch.setattr(engine, "_reset_state", lambda: pytest.fail("negative reached reset"))
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
            strict=True,
        )
    )
    assert calls == tape == []


@pytest.mark.parametrize("failing_callback", ["on_bar_start", "on_equity", "on_bar_end"])
def test_compiled_tick_required_commit_survives_disabled_callbacks(compiled, failing_callback):
    calls = []

    def fail(*args):
        calls.append(args)
        raise ValueError("optional subscriber failed")

    engine, owner, _, result = run(
        compiled,
        cut=False,
        changes={"callback_error_policy": "disable_callbacks"},
        callbacks=BacktestCallbacks(**{failing_callback: fail}),
    )
    assert result.status == "completed" and engine._callbacks_disabled
    assert len(calls) == 1
    literal_oracle(engine, owner)
    assert result.resume_state.bar_index == 3


@pytest.mark.parametrize("donor", ["tick-count", "primary"])
def test_compiled_tick_valid_donor_owner_cannot_splice_into_broker_cut(
    compiled, donor, monkeypatch
):
    wire, _ = producer(compiled)
    state = JsonResumeStateSerializer().loads(wire)
    config, bars = scenario()
    if donor == "tick-count":
        config.realtime_ticks = [
            Tick(index * 10000, 100, volume=0.25) for index in range(4)
        ] + config.realtime_ticks[3:]
    else:
        bars[0] = replace(bars[0], open=101, high=101, low=101, close=101)
        config.realtime_ticks[:3] = [replace(tick, price=101) for tick in config.realtime_ticks[:3]]
    donor_engine, donor_owner = BacktestEngine(config), session(compiled, config)
    donor_result = donor_engine.run(
        donor_owner.generated_class,
        bars=bars,
        execution_backend=RC6GeneratedExecutionBackend(donor_owner),
    )
    assert donor_result.resume_state.bar_index == 0
    state = replace(state, strategy_state=donor_result.resume_state.strategy_state)
    bad = JsonResumeStateSerializer().dumps(state)
    engine, owner, _, _ = run(compiled)
    _, bars = scenario()
    before, broker = owner.export_state(), projection(engine)
    monkeypatch.setattr(engine, "_reset_state", lambda: pytest.fail("donor splice reached reset"))
    with pytest.raises(ResumeUnsupportedError, match="primary history|tick coordinates"):
        engine.run(
            owner.generated_class,
            bars=bars,
            resume_state=bad,
            execution_backend=RC6GeneratedExecutionBackend(owner),
        )
    assert owner.export_state() == before and projection(engine) == broker


def test_compiled_tick_provisional_cut_refuses_export_before_required_commit(compiled):
    config, bars = scenario()
    engine, owner = BacktestEngine(config), session(compiled, config)
    refusals = []

    def provisional(*args):
        with pytest.raises(ValueError, match="uncommitted"):
            owner.export_state()
        refusals.append(True)

    result = engine.run(
        owner.generated_class,
        bars=bars,
        callbacks=BacktestCallbacks(on_equity=provisional),
        execution_backend=RC6GeneratedExecutionBackend(owner),
    )
    assert refusals == [True] and result.resume_state.bar_index == 0
    assert owner.export_state()["last_event"]["final_tick"] is True


def process(mode, directory):
    folder = Path(directory)
    started = datetime.now(timezone.utc).isoformat()
    if mode == "producer":
        data = tick_descriptor()
        wire, tape = producer(data)
        (folder / "descriptor.json").write_bytes(canonical_json(data))
        (folder / "checkpoint.json").write_bytes(wire)
        details = {
            "wire_bytes": len(wire),
            "prefix": tape,
            "wire_sha256": hashlib.sha256(wire).hexdigest(),
        }
    else:
        data = json.loads((folder / "descriptor.json").read_bytes())
        engine, owner, suffix, _ = run(data, wire=(folder / "checkpoint.json").read_bytes())
        literal_oracle(engine, owner)
        control, whole, _, _ = run(data, cut=False)
        assert projection(engine) == projection(control)
        assert owner.session.semantic_state_hash == whole.session.semantic_state_hash
        assert owner.execution_cursor == whole.execution_cursor
        details = {
            "suffix": suffix,
            "effects": projection(engine),
            "literal_oracle": True,
            "control_agrees": True,
            "runtime_hash": owner.session.semantic_state_hash,
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


def test_compiled_tick_exporter_exits_before_new_public_consumer(tmp_path):
    outcomes = []
    for mode in ("producer", "consumer"):
        result = subprocess.run(  # noqa: S603 -- fixed local probe and interpreter
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
