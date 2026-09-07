"""Artifact-admitted nominal declarations survive host execution and restore.

Forged checkpoints have their unkeyed checksums recomputed. Rejection must come
from the admitted declaration, not a stale checksum or an in-process substitute
for the separate mandatory protected-worker tests.
"""

from copy import deepcopy
import json

import pytest
from pinelib.errors import PineRuntimeError
from pinelib.state.checkpoint import RuntimeCheckpoint, sha

from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_nominal_types import prepare
from rc6_tests.test_rc6_reference_loop_values import advance


@pytest.fixture(scope="module", params=[(5, False), (5, True), (6, False), (6, True)])
def nominal_case(request):
    version, imported = request.param
    return prepare(version, imported)


@pytest.fixture(scope="module")
def full_checkpoint(nominal_case):
    case, rows = nominal_case
    session = make_session(case)
    session.session.commit_full_identity = True
    advance(session, rows, stop=3)
    checkpoint = json.loads(json.dumps(session.export_state()))
    assert (
        checkpoint["runtime"]["state"]["transcript"]["schema_id"]
        == "openpine.runtime_transcript.v1"
    )
    return checkpoint


def _reseal(checkpoint):
    runtime = checkpoint["runtime"]
    state = runtime["state"]
    body = {key: value for key, value in state.items() if key != "transcript"}
    transcript = state["transcript"]
    transcript["entries"][-1]["state_hash"] = sha(body)
    transcript["content_hash"] = sha({"entries": transcript["entries"]})
    checkpoint["runtime"] = RuntimeCheckpoint.seal(runtime["identity_hash"], state).to_dict()
    checkpoint["content_hash"] = sha(
        {key: value for key, value in checkpoint.items() if key != "content_hash"}
    )
    return checkpoint


def _enum_markers(value):
    if isinstance(value, dict):
        if set(value) == {"$pinelib_enum"}:
            yield value["$pinelib_enum"]
        for child in value.values():
            yield from _enum_markers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _enum_markers(child)


def test_host_admits_complete_registry_before_any_callback(nominal_case):
    case, _ = nominal_case
    session = make_session(case)
    registry = session.session.nominal_registry
    assert session.session.sequence == -1
    assert registry is not None
    assert registry.to_json() == session.namespace["NOMINAL_TYPE_REGISTRY"]
    assert registry.source_hash == case[0].generated_artifact["source_hash"]
    assert registry.pine_version == case[0].generated_artifact["version_context"]["pine_version"]
    enums = [row for row in registry.to_json()["types"] if row["kind"] == "enum"]
    assert len(enums) == 1
    assert enums[0]["members"] == [{"name": "up", "title": "Up"}, {"name": "down", "title": "Down"}]
    exported = registry.to_json()
    exported["types"].clear()
    assert registry.to_json()["types"]


def test_resealed_unchanged_checkpoint_remains_valid(nominal_case, full_checkpoint):
    case, _ = nominal_case
    session = make_session(case)
    registry = session.session.nominal_registry
    session.restore_state(_reseal(deepcopy(full_checkpoint)))
    assert session.session.nominal_registry is registry
    assert session.export_state() == full_checkpoint


@pytest.mark.parametrize(
    "fault", ["unknown_member", "ordinal", "member_ordinal", "field_type", "field_varip"]
)
def test_forged_nominal_checkpoint_cannot_redeclare_source_types(
    nominal_case, full_checkpoint, fault
):
    case, rows = nominal_case
    checkpoint = deepcopy(full_checkpoint)
    state = checkpoint["runtime"]["state"]
    if fault in {"unknown_member", "ordinal", "member_ordinal"}:
        markers = list(_enum_markers(state))
        assert markers
        for marker in markers:
            if fault == "unknown_member":
                marker["member"] = "undeclared"
            elif fault == "ordinal":
                marker["ordinal"] = 123
            else:
                marker.update(member="up", ordinal=1)
        expected = "enum"
    else:
        objects = [row for row in state["references"]["objects"] if row["kind"] == "udt"]
        assert objects
        for row in objects:
            if fault == "field_type":
                row["udt_schema"]["fields"]["total"] = "string"
                row["working"]["total"] = "forged"
                row["committed"]["total"] = "forged"
            else:
                # Preserve the old wire canonicality so only the declaration
                # check can reject this additional persistence permission.
                row["udt_schema"]["varip_fields"] = ["ticks", "total"]
        expected = "UDT|udt|schema"
    session = make_session(case)
    # Reject into a nonempty session, preserving its registry, transcript and
    # output cursors together; an empty-state-only check would miss replacement.
    advance(session, rows, stop=1)
    before = session.export_state()
    registry = session.session.nominal_registry
    with pytest.raises(PineRuntimeError, match=expected):
        session.restore_state(_reseal(checkpoint))
    assert session.session.nominal_registry is registry
    assert session.export_state() == before


def test_compact_restore_reuses_registry_and_preserves_exact_intent_trace(nominal_case):
    case, rows = nominal_case
    whole = make_session(case)
    expected = advance(whole, rows)
    prefix = make_session(case)
    first = advance(prefix, rows, stop=3)
    saved = json.loads(json.dumps(prefix.export_state()))
    restored = make_session(case)
    registry = restored.session.nominal_registry
    restored.restore_state(saved)
    assert restored.session.nominal_registry is registry
    assert first + advance(restored, rows, start=3) == expected
    assert restored.export_state() == whole.export_state()
    assert len(expected) == 1 and float(expected[0]["qty"]) == 3


@pytest.mark.parametrize("version", [5, 6])
def test_native_executor_admits_registry_before_nominal_constructor(version):
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
    from openpine.runtime.rc6_executor import RC6RuntimeExecutor
    from pinelib import RuntimeLanguageContext
    from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
    from pinelib.state.checkpoint import from_portable

    result = NativeRC6CompilerAdapter().compile(
        f'//@version={version}\nindicator("registry native")\n'
        "type Counter\n    int n=1\nvar Counter c=Counter.new()\nc.n+=1\nplot(c.n)\n",
        module_name="registry_native",
        source_name="registry-native.pine",
        producer_commits={"pine2ast": "1" * 40, "ast2python": "2" * 40},
    )
    assert result.success, result.errors
    artifact = {
        name: getattr(result, name)
        for name in (
            "generated_artifact",
            "python_code",
            "consumer_bundle",
            "source_map",
            "compile_meta",
        )
    }
    executor = RC6RuntimeExecutor(
        artifact=artifact,
        language=RuntimeLanguageContext(
            version,
            "registry-native",
            f"pine-v{version}",
            result.generated_artifact["version_context"]["catalog_hash"],
            "compiler_annotation",
        ),
        instrument=InstrumentContext(
            ticker="SOLUSDT",
            tickerid="BINANCE:SOLUSDT",
            prefix="BINANCE",
            currency="USDT",
            basecurrency="SOL",
            timezone="UTC",
            instrument_type="crypto",
            mintick=0.01,
        ),
        timeframe=TimeframeContext.parse("1"),
    )
    assert executor.session.nominal_registry is not None
    assert executor.session.sequence == -1
    for index in range(2):
        executor.execute_bar(
            BarValues(1, 2, 0, 1, 1, index * 60000, (index + 1) * 60000 - 1),
            bar_index=index,
            last_bar_index=1,
        )
    assert [from_portable(e.payload["series"]) for e in executor.session.visuals.committed] == [
        2,
        3,
    ]


@pytest.mark.parametrize("version", [5, 6])
def test_cached_request_resume_and_extracted_child_restore_keep_registry(version):
    """Cache continuation and actual child restore are separate assertions.

    The immutable provider evaluates all three requested bars on its first use.
    Resuming the host at chart bar 7 therefore reuses cached results; it does not
    enter CompiledRequestExpression's saved-child restore branch. Restore the
    real extracted child checkpoint separately through the public RuntimeSession
    API. Runtime's direct compiled-request tests cover that expression branch.
    """
    from pinelib import RuntimeSession, is_na
    from pinelib.runtime.metadata import TimeframeContext
    from pinelib.state.checkpoint import from_portable

    from rc6_tests.test_rc6_generated_checkpoint import advance as request_advance
    from rc6_tests.test_rc6_generated_checkpoint import session as request_session
    from rc6_tests.test_rc6_requests import ID, case_for

    case, candles = case_for(
        "type ParentCounter\n    int n=0\n"
        "var ParentCounter c=ParentCounter.new()\nc.n+=1\n"
        f'x=request.security("{ID}","5",close)\nplot(x)\n'
        "if (bar_index==4 or bar_index==9 or bar_index==14) and c.n==bar_index+1\n"
        '    strategy.order("requested",strategy.long,qty=x)',
        version=version,
    )
    whole = request_session(case)
    expected = request_advance(whole, candles)
    # Final 5-minute closes align to 1-minute chart bars 4, 9 and 14. The
    # source fixture explicitly supplies 10, 20, 30; no runtime-derived oracle.
    assert [(event["bar_index"], float(event["qty"])) for event in expected] == [
        (4, 10.0),
        (9, 20.0),
        (14, 30.0),
    ]
    values = [
        from_portable(event.payload["series"])
        for event in whole.session.visuals.committed
    ]
    assert len(values) == 15 and all(is_na(value) for value in values[:4])
    assert values[4:] == [10.0] * 5 + [20.0] * 5 + [30.0]

    prefix = request_session(case)
    first = request_advance(prefix, candles, stop=7)
    saved = json.loads(json.dumps(prefix.export_state()))
    assert [(event["bar_index"], float(event["qty"])) for event in first] == [(4, 10.0)]
    datasets = saved["runtime"]["state"]["requests"]["registry"]["datasets"]
    assert len(datasets) == 1
    child_saved = datasets[0]["child_state"]["compiled-runtime"]
    assert child_saved["state"]["sequence"] == 2
    assert (
        child_saved["state"]["transcript"]["schema_id"]
        == "openpine.runtime_transcript.v2"
    )
    assert child_saved["state"]["series"]["close"]["committed"] == [10.0, 20.0, 30.0]

    parent = prefix.session
    registry = parent.nominal_registry
    assert (
        registry is not None
        and registry.to_json() == prefix.namespace["NOMINAL_TYPE_REGISTRY"]
    )
    provider = parent.requests.provider
    requested = provider.source(ID, "5")

    def fresh_child(admitted):
        return RuntimeSession(
            parent.language,
            parent.policies,
            inputs=parent.inputs,
            instrument=requested.instrument,
            timeframe=TimeframeContext.parse(requested.timeframe),
            request_provider=provider,
            nominal_registry=admitted,
        )

    child = fresh_child(registry)
    assert child.sequence == -1 and child.nominal_registry is registry
    assert child.identity_hash == child_saved["identity_hash"]
    child.restore(child_saved)
    assert child.nominal_registry is registry
    assert child.checkpoint().to_dict() == child_saved
    missing = fresh_child(None)
    before = missing.checkpoint().to_dict()
    with pytest.raises(PineRuntimeError, match="identity"):
        missing.restore(child_saved)
    assert missing.checkpoint().to_dict() == before

    resumed = request_session(case)
    resumed_registry = resumed.session.nominal_registry
    resumed.restore_state(saved)
    assert resumed.session.nominal_registry is resumed_registry
    assert first + request_advance(resumed, candles, start=7) == expected
    assert resumed.export_state() == whole.export_state()
