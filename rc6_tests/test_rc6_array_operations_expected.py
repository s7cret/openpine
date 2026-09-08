"""Frozen literal array operations through real ABI and generated lifecycle paths."""
import ast
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib
import inspect
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
CORPUS = ROOT / "verification/builtin-array-operations-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
PATHS = ("abi", "compiled_historical", "compiled_realtime", "compiled_rollback", "compiled_checkpoint")
GAP_BINDINGS = {
    (6, "pine:function:array.binary_search", "pine:function:array.binary_search#canonical", "NAMESPACE_FUNCTION"),
    (6, "pine:method:array.binary_search", "pine:method:array.binary_search#canonical", "METHOD"),
}
GAP_CASES = {f"binary-{result}-v6-{form}" for result in ("found", "missing") for form in ("namespace", "method")}
assert MANIFEST["content_hash"] == LOCK["content_hash"]


def source_identity():
    """Formal runs use declared dependency pins, independently verified by CI."""
    pins = read_json(ROOT / "docs/RC6_LIFECYCLE_SOURCES.json")
    return {"mode": "declared_dependency_pins", **{name: pins[name] for name in ("pine2ast", "ast2python", "pinelib")}}


def declaration_storage(compiled, expected):
    """Observe actual emitted literals through admitted declarations/source maps."""
    declarations = {node.source.node_id: node.attributes["fields"]["name"]
                    for node in compiled.plan.nodes.values()
                    if node.attributes.get("ast_kind") == "VarDeclaration"
                    and node.attributes.get("scope_id") == "scope:global"
                    and node.attributes["fields"]["name"] in expected}
    assert set(declarations.values()) == set(expected)
    calls = [node for node in ast.walk(ast.parse(compiled.emitted.code))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {"declare_reference_v1", "declare_scalar_v1"}]
    result = {}
    for node_id, name in declarations.items():
        locations = [(entry.python_start.line, entry.python_end.line)
                     for entry in compiled.emitted.source_map.entries if entry.source_node_id == node_id]
        selected = [call for call in calls if any(start <= call.lineno <= end for start, end in locations)]
        assert len(selected) == 1
        literal = selected[0].args[0]
        assert isinstance(literal, ast.Constant) and type(literal.value) is str
        assert selected[0].func.attr == ("declare_reference_v1" if expected[name] == "reference" else "declare_scalar_v1")
        result[name] = literal.value
    assert len(set(result.values())) == len(expected)
    return result


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(case for case in MANIFEST["cases"] if case["id"] == case_id)
    settings = read_json(CORPUS.parent / case["settings"]["path"])
    source = (CORPUS.parent / case["source"]["path"]).read_text(encoding="utf-8")
    identity = source_identity()
    bundle = build_consumer_bundle(source, source_name=settings["source_name"], producer_commit=identity["pine2ast"])
    # Select the actual target call's source location, excluding setup pushes.
    primary_nodes = {node["node_id"] for node in bundle["node_index"]
                     if node["kind"] == "CallExpr" and node["span"]["start_line"] == settings["primary_source_line"]}
    primary = [call for call in bundle["semantic_facts"]["calls"] if call["node_id"] in primary_nodes]
    assert len(primary) == 1
    key = tuple(primary[0][field] for field in ("symbol_id", "overload_id", "call_form"))
    assert key == tuple(settings["declared_binding"])
    expected_return = ("void" if settings["result_kind"] == "void" else
                       "array<" + settings["element_type"] + ">" if settings["result_kind"] == "reference" else settings["storage"]["result"])
    assert primary[0]["return_type"] == expected_return
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert binding.python_module + "." + binding.python_name == settings["abi_callable"]
    compiled = compile_consumer_bundle(bundle, target=target,
        expected_pine2ast_commit=identity["pine2ast"], producer_commit=identity["ast2python"])
    storage = declaration_storage(compiled, settings["storage"])
    namespace = {}
    exec(compile(compiled.emitted.code, settings["source_name"] + ".py", "exec"), namespace)
    function = getattr(importlib.import_module(binding.python_module), binding.python_name)
    return settings, bundle, key, target, compiled, storage, namespace, identity, function


def _handle(value):
    if isinstance(value, ReferenceHandle):
        return value
    assert type(value) is dict and set(value) == {"$pinelib_ref"}
    marker = value["$pinelib_ref"]
    assert type(marker) is dict and set(marker) == {"object_id", "kind"}
    assert type(marker["object_id"]) is str and marker["kind"] == "array"
    return ReferenceHandle(**marker)


def snapshot(runtime, storage):
    handles = {name: _handle(runtime.series[storage[name]].read()) for name in ("a", "b") if name in storage}
    values = {name: runtime.references.read_payload(handle) for name, handle in handles.items()}
    if "result" in storage:
        values["result"] = runtime.series[storage["result"]].read()
    if "b" in handles:
        values["same_id"] = handles["a"] == handles["b"]
    return values


def typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(typed_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def execute_case(case, path, compact):
    settings, bundle, key, target, compiled, storage, namespace, identity, function = prepare(case["id"])

    def make_runtime():
        runtime = RuntimeSession(RuntimeLanguageContext(case["pine_version"], "array-operations-manual", f"pine-v{case['pine_version']}",
                                                       compiled.plan.source_hash, "compiler_annotation"))
        runtime.commit_full_identity = not compact
        return runtime

    runtime = make_runtime()
    sequence = 0
    trials, restores = [], []

    def abi_program(tx, bar):
        dtype = "array<" + settings["element_type"] + ">"
        a = tx.declare_reference_v1(storage["a"], "var", lambda: ref.array_new_v1(tx, "abi:a", settings["element_type"]), dtype)
        b = None
        if "b" in storage:
            b = tx.declare_reference_v1(storage["b"], "var", lambda: ref.array_new_v1(tx, "abi:b", settings["element_type"]), dtype)
        if "result" in storage:
            result_type = settings["storage"]["result"]
            initial = {"int": 0, "float": 0.0, "bool": False, "string": ""}[result_type]
            tx.declare_scalar_v1(storage["result"], "var", lambda: initial, result_type)
        if bar == 0:
            for value in settings["before"]:
                ref.array_push_v1(tx, a, value)
        elif bar == 1:
            # Exact public ABI, with its declared allocation parameter only.
            allocation = {"new_object_id": "abi:operation-result"} if "new_object_id" in inspect.signature(function).parameters else {}
            result = function(tx, a, *settings["arguments"], **allocation)
            if settings["result_kind"] == "reference":
                assert isinstance(result, ReferenceHandle)
                tx.write_reference_v1(storage["b"], "var", result, dtype)
            elif settings["result_kind"] == "value":
                tx.write_scalar_v1(storage["result"], "var", result, settings["storage"]["result"])
            else:
                assert result is None
        elif bar - 2 < len(settings["followups"]):
            followup = settings["followups"][bar - 2]
            assert followup["operation"] == "set"
            receiver = a if followup["receiver"] == "a" else b
            ref.array_set_v1(tx, receiver, *followup["arguments"])

    def callback(bar, trial=False):
        nonlocal sequence
        realtime = path in {"compiled_realtime", "compiled_rollback"} and bar > 0
        tx = runtime.begin(CallbackFrame("REALTIME_TICK" if realtime else "HISTORICAL_EVAL", sequence,
                           bar_index=bar, realtime=realtime, final_tick=not trial),
                           values=BarValues(1, 2, 0, 1, 1, bar * 60000, (bar + 1) * 60000 - 1))
        if path == "abi":
            abi_program(tx, bar)
        else:
            namespace["GeneratedScript"](tx).run()
        actual = snapshot(runtime, storage)
        if trial and path == "compiled_rollback":
            tx.abort()
        else:
            tx.commit()
            sequence += 1
        return actual

    def restore_json(bar, reason):
        nonlocal runtime
        before = snapshot(runtime, storage)
        saved = runtime.checkpoint().to_dict()
        clone = make_runtime()
        clone.restore(json.loads(json.dumps(saved)))
        assert clone.checkpoint().to_dict() == saved
        assert typed_equal(snapshot(clone, storage), before)
        runtime = clone
        restores.append({"bar": bar, "reason": reason, "sequence": runtime.sequence, "values_and_aliases_preserved": True})

    events = []
    for bar in read_json(CORPUS.parent / case["data"]["path"]):
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            baseline = snapshot(runtime, storage)
            transcript = deepcopy(runtime.transcript.to_dict())
            previous_sequence, attempted_sequence = runtime.sequence, sequence
            trial = callback(bar, True)
            trials.append({"bar": bar, "value": trial, "attempt_sequence": attempted_sequence})
            if path == "compiled_rollback":
                assert runtime.sequence == previous_sequence and sequence == attempted_sequence
                assert runtime.transcript.to_dict() == transcript
                assert typed_equal(snapshot(runtime, storage), baseline)
                restore_json(bar, "aborted_same_sequence_retry")
        actual = callback(bar)
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            assert typed_equal(trial, actual)
        events.append({"bar": bar, "value": actual})
        if path == "compiled_checkpoint" and bar == 1:
            restore_json(bar, "after_operation_before_authored_followup")
    return {"status": "completed", "compile": True, "events": events, "execution_path": path,
            "transcript_mode": "compact" if compact else "full", "trials": trials, "restores": restores,
            "executed_bindings": [[case["pine_version"], *key]], "target_manifest_hash": target.content_hash,
            "catalog_hash": bundle["version_context"]["catalog_hash"], "source_identity": identity,
            "generated_module_sha256": hashlib.sha256(compiled.emitted.code.encode()).hexdigest(),
            "artifact_hash": compiled.artifact.payload["content_hash"],
            **{key + "_sha256": case[key]["sha256"] for key in ("source", "data", "settings")}}


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_independent_array_payload_scalar_type_and_alias(case, path, compact, observations):
    actual = execute_case(case, path, compact)
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    projection = {"compile": actual["compile"], "events": actual["events"]}
    matches = typed_equal(expected, projection)
    if not matches:
        actual["status"] = "TYPED_VALUE_MISMATCH"
    observations.setdefault((compact, path), {})[case["id"]] = actual
    assert first_difference(expected, projection, case["tolerance"]) is None
    assert matches, (case["id"], expected, projection)


def validate_gap_ledger(ledger):
    """Closed reporting exception; it never changes the static surface or oracle."""
    assert type(ledger) is dict and set(ledger) == {"schema_id", "rows", "tuple_count", "case_count", "full_stage2_accepted"}
    assert ledger["schema_id"] == "openpine.array_operations_contract_gaps.v1"
    assert type(ledger["tuple_count"]) is int and ledger["tuple_count"] == 2
    assert type(ledger["case_count"]) is int and ledger["case_count"] == 4
    assert ledger["full_stage2_accepted"] is False
    assert type(ledger["rows"]) is list and len(ledger["rows"]) == 4
    found = set()
    for row in ledger["rows"]:
        assert type(row) is dict and set(row) == {"case_id", "binding", "surface_status", "reasons", "assigned", "scope"}
        assert type(row["case_id"]) is str and row["case_id"] in GAP_CASES and row["case_id"] not in found
        found.add(row["case_id"])
        assert type(row["binding"]) is list and len(row["binding"]) == 4
        assert type(row["binding"][0]) is int and all(type(part) is str for part in row["binding"][1:])
        assert tuple(row["binding"]) in GAP_BINDINGS
        assert (row["binding"][3] == "METHOD") == row["case_id"].endswith("-method")
        assert row["surface_status"] == "UNAVAILABLE"
        assert row["reasons"] == ["A2P_PINELIB_SOURCE_PARAMETER"]
        assert row["assigned"] is False and row["scope"] == "executed_without_sort_field_only"
    assert found == GAP_CASES


@pytest.fixture(scope="module")
def observations():
    rows = {}
    yield rows
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):
        return
    surface = build_builtin_surface()
    indexed = {(row["pine_version"], row["symbol_id"], row["overload_id"], row["call_form"]): row for row in surface["rows"]}
    write_json(Path(output) / "builtin-array-operations-surface.json", surface)
    for (compact, path), values in rows.items():
        assert len(values) == 191
        assignments, gaps = [], []
        for case_id, value in values.items():
            for key in value["executed_bindings"]:
                row = indexed[tuple(key)]
                if tuple(key) in GAP_BINDINGS:
                    gaps.append({"case_id": case_id, "binding": key, "surface_status": row["status"],
                                 "reasons": row["reasons"], "assigned": False, "scope": "executed_without_sort_field_only"})
                    continue
                assert row["status"] == "RUNTIME_DIRECT"
                assignments.append({**{field: row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form", "contract_hash")},
                                    "case_id": case_id, "path": path})
        ledger = {"schema_id": "openpine.array_operations_contract_gaps.v1", "rows": gaps,
                  "tuple_count": 2, "case_count": 4, "full_stage2_accepted": False}
        validate_gap_ledger(ledger)
        assert len(assignments) == 187
        assert len({tuple(row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form")) for row in assignments}) == 87
        report = builtin_evidence_report(surface, CORPUS, values, corpus_hash=LOCK["content_hash"], assignments=assignments)
        directory = Path(output) / "builtin-array-operations-reports" / ("compact" if compact else "full") / path
        write_json(directory / "observations.json", values)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "unassigned-contract-gaps.json", ledger)
        write_json(directory / "report.json", report)
        assert report["execution_evidence"]["all_assigned_passed"]
        assert not report["full_builtin_expected_accepted"]


def test_array_expansion_and_primary_only_provenance_are_frozen():
    manual = CORPUS.parent / LOCK["manual_table"]["path"]
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == "556d6958a9a77cbffda0374cbf24bdca10dcaf7ddbdea8a853edd53f74bccc6b"
    assert len(MANIFEST["cases"]) == LOCK["case_count"] == 191
    table = read_json(manual)
    assert len(table["rows"]) + len(table["reference_rows"]) == LOCK["manual_case_count"] == 39
    keys = set()
    for case in MANIFEST["cases"]:
        settings = read_json(CORPUS.parent / case["settings"]["path"])
        keys.add((case["pine_version"], *settings["declared_binding"]))
        assert settings["manual_table"] == LOCK["manual_table"]
        assert case["oracle"]["kind"] == "manual_fixture"
        assert len(read_json(CORPUS.parent / case["expected"]["path"])["events"]) == 4
    assert len(keys) == LOCK["versioned_tuple_count"] == 89
    assert MANIFEST["profile"] == "engineering" and LOCK["full_stage2_accepted"] is False


@pytest.mark.parametrize("left,right", [([False], [0]), ([1], [1.0]), ([1, 2], [2, 1]),
                                       (["x"], ["y"]), ({"same_id": True}, {"same_id": False}), ([1], [1, 2])])
def test_typed_observer_rejects_same_size_wrong_values_and_false_alias(left, right):
    assert not typed_equal(left, right)


@pytest.mark.parametrize("corruption", ["reason", "binding", "status", "missing", "duplicate", "extra", "assigned", "count"])
def test_gap_ledger_rejects_forged_or_hidden_contract_gaps(corruption):
    ledger = {"schema_id": "openpine.array_operations_contract_gaps.v1", "tuple_count": 2, "case_count": 4,
              "full_stage2_accepted": False, "rows": [
                  {"case_id": case_id, "binding": list(next(key for key in GAP_BINDINGS if (key[3] == "METHOD") == case_id.endswith("-method"))),
                   "surface_status": "UNAVAILABLE", "reasons": ["A2P_PINELIB_SOURCE_PARAMETER"], "assigned": False,
                   "scope": "executed_without_sort_field_only"} for case_id in sorted(GAP_CASES)]}
    validate_gap_ledger(ledger)
    row = ledger["rows"][0]
    if corruption == "reason":
        row["reasons"] = ["COMPILER_BINDING_MISSING"]
    elif corruption == "binding":
        row["binding"][0] = 5
    elif corruption == "status":
        row["surface_status"] = "RUNTIME_DIRECT"
    elif corruption == "missing":
        ledger["rows"].pop()
    elif corruption == "duplicate":
        ledger["rows"][1] = deepcopy(row)
    elif corruption == "extra":
        row["hidden"] = True
    elif corruption == "assigned":
        row["assigned"] = True
    else:
        ledger["case_count"] = True
    with pytest.raises(AssertionError):
        validate_gap_ledger(ledger)
