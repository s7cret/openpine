"""Host receipts bind native control; native abort resume is not host publication."""

from copy import deepcopy

import pytest
from pinelib import CallbackFrame
from pinelib.state.checkpoint import RuntimeCheckpoint, sha

from rc6_tests.test_rc6_checkpoint_receipts import reseal
from rc6_tests.test_rc6_deferred_exits import prepare
from rc6_tests.test_rc6_lifecycle import event, make_session, values


@pytest.fixture(params=[5, 6])
def control_case(request):
    case, _ = prepare(
        "varip int ticks=0\nticks+=1\n",
        [(100, 102, 99, 101)] * 3,
        version=request.param,
    )
    return case


def publish(host, index, deferred):
    if deferred:
        host.execute_callback(
            values(index),
            event(index=index, sequence=index, last_bar_index=2, last_historical_bar_index=2),
            strategy_values={},
        )
        host.finalize_bar(index)
    else:
        host.execute_bar(values(index), bar_index=index, last_bar_index=2, strategy_values={})


def abort_native(host, deferred):
    tx = host.session.begin(
        CallbackFrame(
            "REALTIME_EVAL", host.session.sequence + 1, realtime=True,
            final_tick=False, bar_index=1, tick_index=0,
            last_bar_index=2, defer_bar_commit=deferred,
        ),
        values=values(1),
    )
    host.generated_class(tx).run()
    tx.abort()
    saved = host.session.checkpoint().to_dict()
    assert "pending_abort" in saved["state"]
    return saved


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_controlled_boundary_roundtrip_and_continuation(control_case, compact, deferred):
    whole = make_session(control_case)
    whole.session.commit_full_identity = not compact
    publish(whole, 0, deferred)
    saved = whole.export_state()
    assert saved["runtime"]["schema_version"] == "1.1.0"
    clone = make_session(control_case)
    clone.session.commit_full_identity = not compact
    clone.restore_state(saved)
    assert clone.export_state() == saved
    for host in (whole, clone):
        publish(host, 1, deferred)
    assert clone.export_state() == whole.export_state()


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_pending_native_abort_cannot_be_exported_as_host_boundary(control_case, compact, deferred):
    host = make_session(control_case)
    host.session.commit_full_identity = not compact
    publish(host, 0, deferred)
    assert host.execution_cursor.open_bar is None
    native = abort_native(host, deferred)
    with pytest.raises(ValueError, match="pending abort"):
        host.export_state()
    assert host.session.checkpoint().to_dict() == native


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_native_abort_cannot_replace_live_host_after_outer_rehash(control_case, compact, deferred):
    source = make_session(control_case)
    source.session.commit_full_identity = not compact
    publish(source, 0, deferred)
    damaged = source.export_state()
    damaged["runtime"] = abort_native(source, deferred)
    reseal(damaged)
    target = make_session(control_case)
    target.session.commit_full_identity = not compact
    publish(target, 0, deferred)
    before = target.export_state()
    owners = (target.session, target.execution_cursor, target._callback_receipts)
    with pytest.raises(ValueError, match="pending abort"):
        target.restore_state(damaged)
    assert all(a is b for a, b in zip(
        owners, (target.session, target.execution_cursor, target._callback_receipts), strict=True
    ))
    assert target.export_state() == before


def rewrite_full_transcript(saved, edit):
    result = deepcopy(saved)
    native = result["runtime"]
    transcript = native["state"]["transcript"]
    assert transcript["schema_id"] == "openpine.runtime_transcript.v1"
    edit(transcript["entries"])
    transcript["content_hash"] = sha({"entries": transcript["entries"]})
    result["runtime"] = RuntimeCheckpoint.seal(
        native["identity_hash"], native["state"], schema_version=native["schema_version"]
    ).to_dict()
    return reseal(result)


def test_controlled_callback_phase_is_not_a_publication_marker(control_case):
    host = make_session(control_case)
    host.session.commit_full_identity = True
    publish(host, 0, False)
    # Phase remains metadata; the runtime's explicit boundary is authoritative.
    saved = rewrite_full_transcript(
        host.export_state(), lambda rows: rows[0].update(phase="BAR_COMMIT")
    )
    clone = make_session(control_case)
    clone.session.commit_full_identity = True
    clone.restore_state(saved)
    assert clone.export_state() == saved


def test_rehashed_control_mode_cannot_disagree_with_direct_receipt(control_case):
    host = make_session(control_case)
    host.session.commit_full_identity = True
    publish(host, 0, False)
    saved = rewrite_full_transcript(
        host.export_state(),
        lambda rows: rows[0]["control"].update(bar_commit_mode="deferred"),
    )
    clone = make_session(control_case)
    clone.session.commit_full_identity = True
    before = clone.export_state()
    with pytest.raises(ValueError, match="control mode"):
        clone.restore_state(saved)
    assert clone.export_state() == before
