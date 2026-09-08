"""Frozen ordered maps, typed scalar returns and copy identity through real code."""
import ast
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path

import pytest
from ast2python import compile_consumer_bundle
from ast2python.lowering import load_pinelib_target_manifest
from pine2ast.hardening.consumer_bundle import build_consumer_bundle
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.abi import reference as ref
from pinelib.abi.manifest import load_target_manifest
from pinelib.reference.heap import ReferenceHandle
from pinelib.runtime.metadata import BarValues

from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-map-operations-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
PATHS = ("abi", "compiled_historical", "compiled_realtime", "compiled_rollback", "compiled_checkpoint")
assert MANIFEST["content_hash"] == LOCK["content_hash"]


def source_identity():
    """Formal CI independently verifies these declared component pins."""
    pins = read_json(ROOT / "docs/RC6_LIFECYCLE_SOURCES.json")
    return {"mode": "declared_dependency_pins", **{name: pins[name] for name in ("pine2ast", "ast2python", "pinelib")}}


def declaration_storage(compiled, expected):
    """Observe actual literal storage IDs through admitted declarations/source maps."""
    declarations = {node.source.node_id: node.attributes["fields"]["name"]
                    for node in compiled.plan.nodes.values()
                    if node.attributes.get("ast_kind") == "VarDeclaration"
                    and node.attributes.get("scope_id") == "scope:global"
                    and node.attributes["fields"]["name"] in expected}
    assert set(declarations.values()) == set(expected)
    calls = [node for node in ast.walk(ast.parse(compiled.emitted.code))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {"declare_reference_v1", "declare_scalar_v1"}]
    storage = {}
    for node_id, name in declarations.items():
        locations = [(entry.python_start.line, entry.python_end.line)
                     for entry in compiled.emitted.source_map.entries if entry.source_node_id == node_id]
        selected = [call for call in calls if any(start <= call.lineno <= end for start, end in locations)]
        assert len(selected) == 1
        literal = selected[0].args[0]
        assert isinstance(literal, ast.Constant) and type(literal.value) is str
        assert selected[0].func.attr == ("declare_reference_v1" if expected[name] == "reference" else "declare_scalar_v1")
        storage[name] = literal.value
    assert len(set(storage.values())) == len(expected)
    return storage


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(row for row in MANIFEST["cases"] if row["id"] == case_id)
    settings = read_json(CORPUS.parent / case["settings"]["path"])
    source = (CORPUS.parent / case["source"]["path"]).read_text(encoding="utf-8")
    identity = source_identity()
    bundle = build_consumer_bundle(source, source_name=settings["source_name"], producer_commit=identity["pine2ast"])
    nodes = {node["node_id"] for node in bundle["node_index"]
             if node["kind"] == "CallExpr" and node["span"]["start_line"] == settings["primary_source_line"]}
    primary = [call for call in bundle["semantic_facts"]["calls"] if call["node_id"] in nodes]
    assert len(primary) == 1
    key = tuple(primary[0][field] for field in ("symbol_id", "overload_id", "call_form"))
    assert key == tuple(settings["declared_binding"])
    dtype = "map<" + settings["key_type"] + "," + settings["value_type"] + ">"
    expected_return = ("void" if settings["result_kind"] == "void" else
                       dtype if settings["result_kind"] == "reference" else settings["storage"]["result"])
    assert primary[0]["return_type"] == expected_return
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert binding.python_module + "." + binding.python_name == settings["abi_callable"]
    compiled = compile_consumer_bundle(bundle, target=target,
        expected_pine2ast_commit=identity["pine2ast"], producer_commit=identity["ast2python"])
    namespace = {}
    exec(compile(compiled.emitted.code, settings["source_name"] + ".py", "exec"), namespace)
    function = getattr(importlib.import_module(binding.python_module), binding.python_name)
    return settings, bundle, key, target, compiled, declaration_storage(compiled, settings["storage"]), namespace, identity, function


def handle_value(value):
    if isinstance(value, ReferenceHandle):
        assert value.kind == "map"
        return value
    assert type(value) is dict and set(value) == {"$pinelib_ref"}
    marker = value["$pinelib_ref"]
    assert type(marker) is dict and set(marker) == {"object_id", "kind"}
    assert type(marker["object_id"]) is str and marker["kind"] == "map"
    return ReferenceHandle(**marker)


def scalar_value(value):
    if value is na:
        return {"kind": "na"}
    assert type(value) in {bool, int, float, str}
    if type(value) is float:
        assert math.isfinite(value)
    return {"kind": "value", "value": value}


def snapshot(runtime, storage):
    handles = {name: handle_value(runtime.series[storage[name]].read()) for name in ("a", "b") if name in storage}
    values = {name: runtime.references.read_payload(handle) for name, handle in handles.items()}
    # The public owner returns ordered key/value pairs, not a lossy Python dict.
    assert all(type(payload) is list and all(type(pair) is list and len(pair) == 2 for pair in payload) for payload in values.values())
    if "result" in storage:
        values["result"] = scalar_value(runtime.series[storage["result"]].read())
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
    row = settings["manual_row"]

    def make_runtime():
        runtime = RuntimeSession(RuntimeLanguageContext(case["pine_version"], "map-operations-manual", f"pine-v{case['pine_version']}",
                                                       compiled.plan.source_hash, "compiler_annotation"))
        runtime.commit_full_identity = not compact
        return runtime

    runtime = make_runtime()
    sequence = 0
    trials, restores = [], []

    def abi_program(tx, bar):
        dtype = "map<" + settings["key_type"] + "," + settings["value_type"] + ">"
        a = tx.declare_reference_v1(storage["a"], "var", lambda: ref.map_new_v1(tx, "abi:a", dtype), dtype)
        b = None
        if "b" in storage:
            b = tx.declare_reference_v1(storage["b"], "var", lambda: a if row.get("same_id") else ref.map_new_v1(tx, "abi:b", dtype), dtype)
        if "result" in storage:
            result_type = settings["storage"]["result"]
            tx.declare_scalar_v1(storage["result"], "var", lambda: False if result_type == "bool" else na, result_type)
        if bar == 0:
            for pair in row["before"]:
                ref.map_put_v1(tx, a, *pair)
            if settings["operation"] == "put_all" and not row["same_id"]:
                for pair in row["second"]:
                    ref.map_put_v1(tx, b, *pair)
        elif bar == 1:
            allocation = {"new_object_id": "abi:operation-result"} if "new_object_id" in inspect.signature(function).parameters else {}
            args = [*row["arguments"], *([b] if settings["operation"] == "put_all" else [])]
            result = function(tx, a, *args, **allocation)
            if settings["result_kind"] == "reference":
                assert isinstance(result, ReferenceHandle) and result.kind == "map"
                tx.write_reference_v1(storage["b"], "var", result, dtype)
            elif settings["result_kind"] != "void":
                tx.write_scalar_v1(storage["result"], "var", result, settings["storage"]["result"])
            else:
                assert result is None
        elif bar - 2 < len(row.get("followups", [])):
            step = row["followups"][bar - 2]
            assert step["operation"] in {"put", "remove"} and step["receiver"] in {"a", "b"}
            followup = getattr(ref, "map_" + step["operation"] + "_v1")
            followup(tx, a if step["receiver"] == "a" else b, *step["arguments"])

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
        restores.append({"bar": bar, "reason": reason, "sequence": runtime.sequence, "ordered_values_and_aliases_preserved": True})

    events = []
    for bar in read_json(CORPUS.parent / case["data"]["path"]):
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            baseline = snapshot(runtime, storage)
            transcript = deepcopy(runtime.transcript.to_dict())
            previous_sequence, attempted = runtime.sequence, sequence
            trial = callback(bar, True)
            trials.append({"bar": bar, "value": trial, "attempt_sequence": attempted})
            if path == "compiled_rollback":
                assert runtime.sequence == previous_sequence and sequence == attempted
                assert runtime.transcript.to_dict() == transcript and typed_equal(snapshot(runtime, storage), baseline)
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
            **{field + "_sha256": case[field]["sha256"] for field in ("source", "data", "settings")}}


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_independent_map_ordered_payload_scalar_and_alias(case, path, compact, observations):
    actual = execute_case(case, path, compact)
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    projection = {"compile": actual["compile"], "events": actual["events"]}
    matches = typed_equal(expected, projection)
    if not matches:
        actual["status"] = "TYPED_VALUE_MISMATCH"
    observations.setdefault((compact, path), {})[case["id"]] = actual
    assert first_difference(expected, projection, case["tolerance"]) is None
    assert matches, (case["id"], expected, projection)


@pytest.fixture(scope="module")
def observations():
    rows = {}
    yield rows
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):
        return
    surface = build_builtin_surface()
    indexed = {(row["pine_version"], row["symbol_id"], row["overload_id"], row["call_form"]): row for row in surface["rows"]}
    target_rows = load_target_manifest()["rows"]
    write_json(Path(output) / "builtin-map-operations-surface.json", surface)
    for (compact, path), values in rows.items():
        assert len(values) == 112
        assignments = []
        for case_id, value in values.items():
            assert len(value["executed_bindings"]) == 1
            row = indexed[tuple(value["executed_bindings"][0])]
            assert row["status"] == "RUNTIME_DIRECT"
            assignments.append({**{field: row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form", "contract_hash")},
                                "case_id": case_id, "path": path})
        assert len({tuple(row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form")) for row in assignments}) == 32
        report = builtin_evidence_report(surface, CORPUS, values, corpus_hash=LOCK["content_hash"], assignments=assignments)
        directory = Path(output) / "builtin-map-operations-reports" / ("compact" if compact else "full") / path
        write_json(directory / "observations.json", values)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "report.json", report)
        scope = []
        for operation in ("get", "contains", "remove"):
            for form in ("namespace", "method"):
                selected = [item for item in target_rows if item["name"] == "map." + operation
                            and item["category"] == ("methods" if form == "method" else "functions")]
                assert len(selected) == 1
                observed_types = sorted({read_json(CORPUS.parent / case["settings"]["path"])["key_type"]
                                         for case in MANIFEST["cases"]
                                         if read_json(CORPUS.parent / case["settings"]["path"])["operation"] == operation
                                         and case["id"].endswith("-" + form)})
                scope.append({"operation": "map." + operation, "form": form, "pine_versions": [5, 6],
                              "observed_key_types": observed_types, "target_parameters": selected[0]["parameters"],
                              "generic_key_contract_covered": False})
        write_json(directory / "key-contract-scope.json", {"schema_id": "openpine.map_key_contract_scope.v1", "rows": scope,
                   "note": "Exact observed key types do not certify the full generic K source contract; target key metadata is recorded unchanged.",
                   "full_stage2_accepted": False})
        assert report["execution_evidence"]["all_assigned_passed"] and report["trace_comparison"]["ok"]
        assert not report["full_builtin_expected_accepted"]


def test_map_literal_expansion_and_primary_only_provenance_are_frozen():
    manual = CORPUS.parent / LOCK["manual_table"]["path"]
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == "3afd6e3a0a1a0abe972c40190bff3dd2caf566aa5e7a7a355aa9d2acd5bb9ffe"
    assert len(MANIFEST["cases"]) == LOCK["case_count"] == 112
    table = read_json(manual)
    originals = {row["id"]: row for row in table["rows"] + table["reference_rows"]}
    assert len(originals) == LOCK["manual_case_count"] == 28
    keys = set()
    for case in MANIFEST["cases"]:
        settings = read_json(CORPUS.parent / case["settings"]["path"])
        assert typed_equal(settings["manual_row"], originals[settings["manual_case_id"]])
        assert settings["manual_table"] == LOCK["manual_table"]
        assert case["oracle"]["kind"] == "manual_fixture"
        assert len(read_json(CORPUS.parent / case["expected"]["path"])["events"]) == 4
        keys.add((case["pine_version"], *settings["declared_binding"]))
    assert len(keys) == LOCK["versioned_tuple_count"] == 32
    assert MANIFEST["profile"] == "engineering" and LOCK["full_stage2_accepted"] is False


@pytest.mark.parametrize("left,right", [([["a", False]], [["a", 0]]), ([[1, 2]], [[True, 2]]),
    ([["a", 1]], [["a", 1.0]]), ([["a", 1], ["b", 2]], [["b", 2], ["a", 1]]),
    ([["a", "yes"]], [["a", "no"]]), ({"same_id": True}, {"same_id": False}),
    ({"kind": "na"}, {"kind": "value", "value": None}), ([["a", 1]], [["b", 1]]),
    ([["a", 1]], [["a", 1], ["b", 2]])])
def test_typed_map_observer_rejects_wrong_values_order_alias_and_missing_token(left, right):
    assert not typed_equal(left, right)
