"""One committed generated/broker cut binds acknowledged fill causes."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backtest_engine import BacktestCallbacks, BacktestEngine, JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from openpine.runtime.generated_backtest import RC6GeneratedExecutionBackend
from test_rc6_generated_bytes import descriptor, inputs, projection, reseal, session
from test_rc6_generated_ticks import scenario, tick_descriptor


@pytest.fixture(scope="module", params=[False, True], ids=["historical", "ticks"])
def compiled(request):
    return request.param, tick_descriptor() if request.param else descriptor()


def setup(compiled, *, cut=True):
    ticks, data = compiled
    config, bars = scenario() if ticks else inputs()
    return (
        data,
        replace(
            config,
            calc_on_order_fills=True,
            early_stop_enabled=cut,
            min_equity_stop=None,
            max_bars_without_trade=0,
        ),
        bars,
    )


def execute(compiled, *, wire=None, cut=True):
    data, config, bars = setup(compiled, cut=cut)
    engine, owner, tape = BacktestEngine(config), session(data, config), []
    result = engine.run(
        owner.generated_class,
        bars=bars,
        resume_state=wire,
        execution_backend=RC6GeneratedExecutionBackend(owner, tape),
    )
    return engine, owner, tape, result


def export_filled_cut(compiled):
    engine, owner, tape, result = execute(compiled)
    assert result.status == "early_stopped" and result.resume_state.bar_index == 1
    assert [(f.side, f.qty, f.price, f.bar_index) for f in engine.fills] == [("buy", 3.0, 100.0, 1)]
    assert any(r["event"]["cause"] == "ORDER_FILL" for r in owner._callback_receipts)
    return JsonResumeStateSerializer().dumps(result.resume_state), tape


def continue_cut(compiled, wire, tape):
    for index in (2, 3):
        engine, owner, suffix, result = execute(compiled, wire=wire)
        assert result.resume_state.bar_index == index
        tape += suffix
        wire = JsonResumeStateSerializer().dumps(result.resume_state)
    control, whole, all_tape, expected = execute(compiled, cut=False)
    if compiled[0]:
        expected_fills = [("buy", 3.0, 100.0, 1), ("sell", 1.0, 120.0, 2)]
        assert engine.position.realized_profit == 20.0 and engine.equity == 1080.0
    else:
        # This source deliberately repeats a one-unit close during each fill
        # recalculation. Three equal IDs/prices are three legitimate fills.
        expected_fills = [("buy", 3.0, 100.0, 1)] + [("sell", 1.0, 120.0, 2)] * 3
        assert engine.position.direction == "flat"
        assert engine.position.realized_profit == 60.0 and engine.equity == 1060.0
    assert [(f.side, f.qty, f.price, f.bar_index) for f in engine.fills] == expected_fills
    assert tape == all_tape and projection(engine) == projection(control)
    assert owner.session.semantic_state_hash == whole.session.semantic_state_hash
    assert owner.execution_cursor == whole.execution_cursor
    assert result.resume_state.broker_state == replace(
        expected.resume_state.broker_state,
        risk_state=expected.resume_state.broker_state.risk_state | {"_early_stop_enabled": True},
    )
    assert result.resume_state.statistics_state == expected.resume_state.statistics_state
    return engine, owner, tape, result


def test_filled_public_cut_continues_without_replaying_fills_or_callback_effects(compiled):
    wire, tape = export_filled_cut(compiled)
    continue_cut(compiled, wire, tape)


@pytest.mark.parametrize(
    "field,value", [("fill_order_id", "other-valid-order"), ("fill_price", "999")]
)
def test_resealed_owner_valid_fill_cause_cannot_disagree_with_broker(
    compiled, field, value, monkeypatch
):
    wire, _ = export_filled_cut(compiled)
    state = JsonResumeStateSerializer().loads(wire)
    owner_state = state.strategy_state
    event = next(
        r["event"] for r in owner_state["callback_receipts"] if r["event"]["cause"] == "ORDER_FILL"
    )
    event[field] = value
    reseal(owner_state, receipts=True)
    bad = JsonResumeStateSerializer().dumps(state)
    receiver, owner, _, _ = execute(compiled)
    # Both complete individual owners admit this input. Only their common cut
    # can reject the causal disagreement; checksums are not authenticity proofs.
    owner.prepare_restore(owner_state)
    before, broker = owner.export_state(), projection(receiver)
    identities = (
        receiver.position,
        receiver.orders,
        receiver.fills,
        receiver.callbacks,
        owner.session,
        owner.execution_cursor,
        owner._callback_receipts,
    )
    calls, tape = [], []
    monkeypatch.setattr(
        receiver, "_reset_state", lambda: pytest.fail("cross-cut mismatch reached reset")
    )
    monkeypatch.setattr(
        owner.generated_class, "run", lambda *a, **kw: pytest.fail("cross-cut mismatch evaluated")
    )
    _, _, bars = setup(compiled)
    with pytest.raises(ResumeUnsupportedError, match="fill receipt differs"):
        receiver.run(
            owner.generated_class,
            bars=bars,
            resume_state=bad,
            callbacks=BacktestCallbacks(on_bar_start=lambda *a: calls.append(a)),
            execution_backend=RC6GeneratedExecutionBackend(owner, tape),
        )
    assert calls == tape == []
    assert owner.export_state() == before and projection(receiver) == broker
    assert all(
        a is b
        for a, b in zip(
            identities,
            (
                receiver.position,
                receiver.orders,
                receiver.fills,
                receiver.callbacks,
                owner.session,
                owner.execution_cursor,
                owner._callback_receipts,
            ),
            strict=True,
        )
    )


def process(mode, directory, ticks):
    folder = Path(directory)
    started = datetime.now(timezone.utc).isoformat()
    if mode == "producer":
        data = tick_descriptor() if ticks else descriptor()
        wire, tape = export_filled_cut((ticks, data))
        (folder / "descriptor.json").write_text(json.dumps(data) + "\n")
        (folder / "checkpoint.json").write_bytes(wire)
        (folder / "prefix.json").write_text(json.dumps(tape) + "\n")
        details = {
            "wire_bytes": len(wire),
            "wire_sha256": hashlib.sha256(wire).hexdigest(),
            "filled_cut_bar": 1,
            "fills": [["buy", 3.0, 100.0, 1]],
        }
    else:
        data = json.loads((folder / "descriptor.json").read_text())
        engine, owner, tape, result = continue_cut(
            (ticks, data),
            (folder / "checkpoint.json").read_bytes(),
            json.loads((folder / "prefix.json").read_text()),
        )
        details = {
            "effects": projection(engine),
            "final_cursor": result.resume_state.bar_index,
            "runtime_hash": owner.session.semantic_state_hash,
            "literal_control_agrees": True,
        }
    report = {
        "pid": os.getpid(),
        "ticks": ticks,
        "pins": data["pins"],
        "passed": True,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    (folder / (mode + ".json")).write_text(json.dumps(report, indent=2) + "\n")


def test_filled_cut_producer_exits_before_new_public_consumer(compiled, tmp_path):
    ticks, _ = compiled
    reports = []
    for mode in ("producer", "consumer"):
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and local probe
            [
                sys.executable,
                "-B",
                __file__,
                mode,
                str(tmp_path),
                "ticks" if ticks else "historical",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        reports.append(json.loads((tmp_path / (mode + ".json")).read_text()))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["completed_utc"] < reports[1]["started_utc"]


def test_individually_valid_empty_broker_cannot_borrow_filled_generated_owner(monkeypatch):
    compiled = False, descriptor()
    wire, _ = export_filled_cut(compiled)
    filled = JsonResumeStateSerializer().loads(wire)
    data, config, bars = setup(compiled)

    class Silent:
        def __init__(self, params, runtime, ctx):
            pass

        def run_bar(self, bar, index):
            pass

        def export_state(self):
            return {"silent": True}

    donor = BacktestEngine(config).run(Silent, bars=bars[:2]).resume_state
    assert donor.bar_index == filled.bar_index and donor.broker_state.fills == []
    donor = replace(donor, strategy_state=filled.strategy_state)
    bad = JsonResumeStateSerializer().dumps(donor)
    receiver, owner, _, _ = execute(compiled)
    owner.prepare_restore(donor.strategy_state)
    before, broker = owner.export_state(), projection(receiver)
    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("donor broker reached reset"))
    with pytest.raises(ResumeUnsupportedError, match="fill receipt differs"):
        receiver.run(
            owner.generated_class,
            bars=bars,
            resume_state=bad,
            execution_backend=RC6GeneratedExecutionBackend(owner),
        )
    assert owner.export_state() == before and projection(receiver) == broker


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2], sys.argv[3] == "ticks")
