"""Frozen literal arrays through real concat ABI and generated lifecycle paths."""
import ast
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

import pytest
from ast2python import compile_consumer_bundle
from ast2python.lowering import load_pinelib_target_manifest
from pine2ast.hardening.consumer_bundle import build_consumer_bundle
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession
from pinelib.abi import reference as ref
from pinelib.reference.heap import ReferenceHandle
from pinelib.runtime.metadata import BarValues

from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-array-concat-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
PATHS = ("abi", "compiled_historical", "compiled_realtime", "compiled_rollback", "compiled_checkpoint")
assert MANIFEST["content_hash"] == LOCK["content_hash"]


def source_identity():
    """Formal runs consume the same declared dependency pins as the host compiler.

    A separate local validation launcher can replace this test-harness function
    while recording an explicit frozen-content-snapshot identity. These fields
    alone do not prove checkout contents; coordinated CI verifies those pins.
    """
    pins = read_json(ROOT / "docs/RC6_LIFECYCLE_SOURCES.json")
    return {"mode":"declared_dependency_pins", "pine2ast":pins["pine2ast"],
            "ast2python":pins["ast2python"], "pinelib":pins["pinelib"]}


def declaration_storage(compiled):
    """Observe actual emitted storage literals; never guess an ID prefix.

    The lowering plan names the admitted global declarations. Its source map
    identifies the corresponding generated calls. Parsing that generated AST
    is an observation adapter; the executed module remains unchanged.
    """
    declarations = {
        n.source.node_id:n.attributes["fields"]["name"]
        for n in compiled.plan.nodes.values()
        if n.attributes.get("ast_kind") == "VarDeclaration"
        and n.attributes.get("scope_id") == "scope:global"
        and n.attributes["fields"]["name"] in {"a", "b", "c"}
    }
    assert len(declarations) == 3
    tree = ast.parse(compiled.emitted.code)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "declare_reference_v1"]
    result = {}
    for node_id, name in declarations.items():
        locations = [(m.python_start.line, m.python_end.line)
                     for m in compiled.emitted.source_map.entries if m.source_node_id == node_id]
        selected = [c for c in calls if any(start <= c.lineno <= end for start, end in locations)]
        assert len(selected) == 1
        arg = selected[0].args[0]
        assert isinstance(arg, ast.Constant) and type(arg.value) is str
        result[name] = arg.value
    assert len(set(result.values())) == 3
    return result


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(c for c in MANIFEST["cases"] if c["id"] == case_id)
    settings = read_json(CORPUS.parent / case["settings"]["path"])
    source = (CORPUS.parent / case["source"]["path"]).read_text(encoding="utf-8")
    identity = source_identity()
    bundle = build_consumer_bundle(source, source_name=settings["source_name"], producer_commit=identity["pine2ast"])
    key = tuple(settings["declared_binding"])
    calls = [c for c in bundle["semantic_facts"]["calls"]
             if (c["symbol_id"], c["overload_id"], c["call_form"]) == key]
    assert len(calls) == 1
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert f"{binding.python_module}.{binding.python_name}" == settings["abi_callable"]
    compiled = compile_consumer_bundle(bundle, target=target,
        expected_pine2ast_commit=identity["pine2ast"], producer_commit=identity["ast2python"])
    storage = declaration_storage(compiled)
    namespace = {}
    exec(compile(compiled.emitted.code, settings["source_name"]+".py", "exec"), namespace)
    return settings, bundle, key, target, compiled, storage, namespace, identity


def _handle(value):
    if isinstance(value, ReferenceHandle):return value
    # SeriesStorage exposes the established portable reference token after a
    # clone/restore. Decode only that closed transport shape, not Pine values.
    assert type(value) is dict and set(value) == {"$pinelib_ref"}
    marker = value["$pinelib_ref"]
    assert type(marker) is dict and set(marker) == {"object_id", "kind"}
    assert type(marker["object_id"]) is str and marker["kind"] == "array"
    return ReferenceHandle(**marker)


def snapshot(runtime, storage):
    handles = {name:_handle(runtime.series[sid].read()) for name,sid in storage.items()}
    return {"id1":runtime.references.read_payload(handles["a"]),
            "id2":runtime.references.read_payload(handles["b"]),
            "result":runtime.references.read_payload(handles["c"]),
            "result_is_id1":handles["c"] == handles["a"], "id2_is_id1":handles["b"] == handles["a"]}


def typed_equal(expected, actual):
    if type(expected) is not type(actual):return False
    if type(expected) is dict:
        return expected.keys() == actual.keys() and all(typed_equal(expected[k], actual[k]) for k in expected)
    if type(expected) is list:
        return len(expected) == len(actual) and all(typed_equal(a,b) for a,b in zip(expected,actual))
    return expected == actual


def execute_case(case, path, compact):
    settings, bundle, key, target, compiled, storage, namespace, identity = prepare(case["id"])

    def new_runtime():
        runtime = RuntimeSession(RuntimeLanguageContext(case["pine_version"], "concat-manual-fixtures",
            f"pine-v{case['pine_version']}", compiled.plan.source_hash, "compiler_annotation"))
        runtime.commit_full_identity = not compact
        return runtime

    runtime = new_runtime()
    sequence = 0
    trial_records, restore_records = [], []

    def abi_program(tx, bar):
        dtype = "array<"+settings["element_type"]+">"
        a = tx.declare_reference_v1(storage["a"], "var", lambda:ref.array_new_v1(tx,"abi:a",settings["element_type"]), dtype)
        b = tx.declare_reference_v1(storage["b"], "var", lambda:a if settings["same_reference"] else ref.array_new_v1(tx,"abi:b",settings["element_type"]), dtype)
        c = tx.declare_reference_v1(storage["c"], "var", lambda:a, dtype)
        if bar == 0:
            for value in settings["id1_initial"]:ref.array_push_v1(tx, a, value)
            if not settings["same_reference"]:
                for value in settings["id2_initial"]:ref.array_push_v1(tx, b, value)
        elif bar == 1:
            c = ref.array_concat_v1(tx, id1=a, id2=b)
            tx.write_reference_v1(storage["c"], "var", c, dtype)
        elif bar == 2:ref.array_push_v1(tx, c, settings["push_through_return"])

    def callback(bar, trial=False):
        nonlocal sequence
        realtime = path in {"compiled_realtime", "compiled_rollback"} and bar > 0
        tx = runtime.begin(CallbackFrame("REALTIME_TICK" if realtime else "HISTORICAL_EVAL",
            sequence, bar_index=bar, realtime=realtime, final_tick=not trial),
            values=BarValues(1,2,0,1,1,bar*60000,(bar+1)*60000-1))
        if path == "abi":abi_program(tx, bar)
        else:namespace["GeneratedScript"](tx).run()
        value = snapshot(runtime, storage)
        if trial and path == "compiled_rollback":tx.abort()
        else:
            tx.commit()
            sequence += 1
        return value

    def restore_json(bar, reason):
        nonlocal runtime
        saved = runtime.checkpoint().to_dict()
        before = snapshot(runtime, storage)
        clone = new_runtime()
        clone.restore(json.loads(json.dumps(saved)))
        assert clone.checkpoint().to_dict() == saved
        assert typed_equal(before, snapshot(clone, storage))
        runtime = clone
        restore_records.append({"bar":bar,"reason":reason,"sequence":runtime.sequence,"alias_preserved":True})

    events = []
    for bar in read_json(CORPUS.parent / case["data"]["path"]):
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            baseline = snapshot(runtime, storage)
            transcript = deepcopy(runtime.transcript.to_dict())
            successful_sequence = runtime.sequence
            attempted_sequence = sequence
            trial = callback(bar, True)
            trial_records.append({"bar":bar,"value":trial,"attempt_sequence":attempted_sequence})
            if path == "compiled_rollback":
                assert runtime.sequence == successful_sequence and sequence == attempted_sequence
                assert runtime.transcript.to_dict() == transcript
                assert typed_equal(snapshot(runtime, storage), baseline)
                restore_json(bar, "aborted_same_sequence_retry")
        value = callback(bar)
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            assert typed_equal(trial, value)
        events.append({"bar":bar,"value":value})
        if path == "compiled_checkpoint" and bar == 1:restore_json(bar, "between_concat_and_alias_push")
    return {"status":"completed","compile":True,"events":events,"execution_path":path,
            "transcript_mode":"compact" if compact else "full","trials":trial_records,"restores":restore_records,
            "executed_bindings":[[case["pine_version"],*key]],"target_manifest_hash":target.content_hash,
            "catalog_hash":bundle["version_context"]["catalog_hash"],"source_identity":identity,
            "generated_module_sha256":hashlib.sha256(compiled.emitted.code.encode()).hexdigest(),
            "artifact_hash":compiled.artifact.payload["content_hash"],
            **{k+"_sha256":case[k]["sha256"] for k in ("source","data","settings")}}


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c:c["id"])
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_independent_concat_full_arrays_and_identity(case, path, compact, observations):
    actual = execute_case(case, path, compact)
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    projection = {"compile":actual["compile"],"events":actual["events"]}
    matches = typed_equal(expected, projection)
    if not matches:actual["status"] = "TYPED_VALUE_MISMATCH"
    observations.setdefault((compact,path), {})[case["id"]] = actual
    assert first_difference(expected, projection, case["tolerance"]) is None
    assert matches, (case["id"], expected, projection)


@pytest.fixture(scope="module")
def observations():
    rows = {}
    yield rows
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):return
    surface = build_builtin_surface()
    indexed = {(r["pine_version"],r["symbol_id"],r["overload_id"],r["call_form"]):r for r in surface["rows"]}
    write_json(Path(output)/"builtin-array-concat-surface.json", surface)
    for (compact,path), values in rows.items():
        assignments = []
        for case_id, value in values.items():
            for key in value["executed_bindings"]:
                row = indexed[tuple(key)]; assert row["status"] == "RUNTIME_DIRECT"
                assignments.append({**{k:row[k] for k in ("pine_version","symbol_id","overload_id","call_form","contract_hash")},
                                    "case_id":case_id,"path":path})
        report = builtin_evidence_report(surface,CORPUS,values,corpus_hash=LOCK["content_hash"],assignments=assignments)
        directory = Path(output)/"builtin-array-concat-reports"/("compact" if compact else "full")/path
        write_json(directory/"observations.json",values)
        write_json(directory/"assignments.json",assignments)
        write_json(directory/"report.json",report)
        assert report["execution_evidence"]["all_assigned_passed"]
        assert not report["full_builtin_expected_accepted"]


def test_concat_expansion_and_manual_provenance_are_frozen():
    manual = CORPUS.parent / LOCK["manual_table"]["path"]
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == "20d833ca9c31265c48c250dbec7edc5a9da3f9b2885e098fd0196e4d4e86ee9e"
    assert len(MANIFEST["cases"]) == LOCK["case_count"] == 50
    assert len(read_json(manual)["cases"]) == LOCK["manual_case_count"] == 30
    keys = set()
    for case in MANIFEST["cases"]:
        settings = read_json(CORPUS.parent/case["settings"]["path"])
        keys.add((case["pine_version"],*settings["declared_binding"]))
        assert settings["manual_table"] == LOCK["manual_table"]
        assert case["oracle"]["kind"] == "manual_fixture"
        assert len(read_json(CORPUS.parent/case["expected"]["path"])["events"]) == 4
    assert len(keys) == LOCK["versioned_tuple_count"] == 5
    assert MANIFEST["profile"] == "engineering" and LOCK["full_stage2_accepted"] is False


@pytest.mark.parametrize("left,right", [([False],[0]),([1],[1.0]),([1,2],[2,1]),(["x"],["y"]),
                                       ({"result_is_id1":True},{"result_is_id1":False}),([1],[1,2])])
def test_full_typed_observation_rejects_same_size_wrong_values_and_false_alias(left,right):
    assert not typed_equal(left,right)
