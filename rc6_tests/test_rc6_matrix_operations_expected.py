"""Literal matrix shape, cells and copy identity through the native compiler adapter."""
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
from ast2python.lowering import load_pinelib_target_manifest
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.abi import reference as ref
from pinelib.reference.heap import ReferenceHandle
from pinelib.runtime.metadata import BarValues

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-matrix-operations-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
PATHS = ("abi", "compiled_historical", "compiled_realtime", "compiled_rollback", "compiled_checkpoint")
assert MANIFEST["content_hash"] == LOCK["content_hash"]


def source_identity():
    """Formal CI independently verifies these declared component pins."""
    pins = read_json(ROOT / "docs/RC6_LIFECYCLE_SOURCES.json")
    return {"mode": "declared_dependency_pins", **{name: pins[name] for name in ("pine2ast", "ast2python", "pinelib")}}


def declaration_storage(compiled, expected):
    """Observe binding literals using the native adapter's admitted facts/map."""
    facts = compiled.consumer_bundle["semantic_facts"]["facts"]
    declarations = {row["node_id"]: row["declaration_target"] for row in facts
                    if row["kind"] == "VarDeclaration" and row["scope_id"] == "scope:global"
                    and row["declaration_target"] in expected}
    assert set(declarations.values()) == set(expected)
    calls = [node for node in ast.walk(ast.parse(compiled.python_code))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {"declare_reference_v1", "declare_scalar_v1"}]
    storage = {}
    for node_id, name in declarations.items():
        locations = [(entry["python_start"]["line"], entry["python_end"]["line"])
                     for entry in compiled.source_map["entries"] if entry["source_node_id"] == node_id]
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
    compiled = NativeRC6CompilerAdapter().compile(source, source_name=settings["source_name"],
        producer_commits={name: identity[name] for name in ("pine2ast", "ast2python")})
    assert compiled.success, compiled.errors
    assert compiled.compile_meta["adapter"] == "native-rc6-python-library"
    bundle = compiled.consumer_bundle
    nodes = {node["node_id"] for node in bundle["node_index"]
             if node["kind"] == "CallExpr" and node["span"]["start_line"] == settings["primary_source_line"]}
    primary = [call for call in bundle["semantic_facts"]["calls"] if call["node_id"] in nodes]
    assert len(primary) == 1
    key = tuple(primary[0][field] for field in ("symbol_id", "overload_id", "call_form"))
    assert key == tuple(settings["declared_binding"])
    dtype = "matrix<" + settings["element_type"] + ">"
    expected_return = ("void" if settings["result_kind"] == "void" else
                       dtype if settings["result_kind"] == "reference" else settings["storage"]["result"])
    assert primary[0]["return_type"] == expected_return
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert binding.python_module + "." + binding.python_name == settings["abi_callable"]
    assert compiled.compile_meta["target_manifest_hash"] == target.content_hash
    namespace = {}
    exec(compile(compiled.python_code, settings["source_name"] + ".py", "exec"), namespace)
    function = getattr(importlib.import_module(binding.python_module), binding.python_name)
    return settings, bundle, key, target, compiled, declaration_storage(compiled, settings["storage"]), namespace, identity, function


def handle_value(value):
    if isinstance(value, ReferenceHandle):
        assert value.kind == "matrix"
        return value
    assert type(value) is dict and set(value) == {"$pinelib_ref"}
    marker = value["$pinelib_ref"]
    assert type(marker) is dict and set(marker) == {"object_id", "kind"}
    assert type(marker["object_id"]) is str and marker["kind"] == "matrix"
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
    values = {}
    for name, handle in handles.items():
        payload = runtime.references.read_payload(handle)
        assert type(payload) is dict and set(payload) == {"rows", "columns", "values"}
        assert all(type(payload[key]) is int and payload[key] >= 0 for key in ("rows", "columns"))
        assert type(payload["values"]) is list and len(payload["values"]) == payload["rows"] * payload["columns"]
        cells = []
        for value in payload["values"]:
            checked = scalar_value(value)
            cells.append(checked if value is na else checked["value"])
        values[name] = {"rows": payload["rows"], "columns": payload["columns"], "values": cells}
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
        runtime = RuntimeSession(RuntimeLanguageContext(case["pine_version"], "matrix-operations-manual", f"pine-v{case['pine_version']}",
                                                       compiled.generated_artifact["source_hash"], "compiler_annotation"))
        runtime.commit_full_identity = not compact
        return runtime

    runtime = make_runtime()
    sequence = 0
    trials, restores = [], []

    def abi_program(tx, bar):
        dtype = "matrix<" + settings["element_type"] + ">"
        payload = row["before"]
        initial = {"int": 0, "float": 0.0, "bool": False, "string": ""}[settings["element_type"]]
        a = tx.declare_reference_v1(storage["a"], "var", lambda: ref.matrix_new_v1(tx, "abi:a", dtype, payload["rows"], payload["columns"], initial), dtype)
        b = None
        if "b" in storage:
            b = tx.declare_reference_v1(storage["b"], "var", lambda: ref.matrix_new_v1(tx, "abi:b", dtype, 0, 0, initial), dtype)
        if "result" in storage:
            result_type = settings["storage"]["result"]
            tx.declare_scalar_v1(storage["result"], "var", lambda: False if result_type == "bool" else na, result_type)

        def argument(value):
            return na if type(value) is dict and value == {"kind": "na"} else value

        if bar == 0:
            for index, value in enumerate(payload["values"]):
                ref.matrix_set_v1(tx, a, index // payload["columns"], index % payload["columns"], argument(value))
        elif bar == 1:
            allocation = {"new_object_id": "abi:operation-result"} if "new_object_id" in inspect.signature(function).parameters else {}
            result = function(tx, a, *[argument(value) for value in row["arguments"]], **allocation)
            if settings["result_kind"] == "reference":
                assert isinstance(result, ReferenceHandle) and result.kind == "matrix"
                tx.write_reference_v1(storage["b"], "var", result, dtype)
            elif settings["result_kind"] != "void":
                tx.write_scalar_v1(storage["result"], "var", result, settings["storage"]["result"])
            else:
                assert result is None
        elif bar - 2 < len(row.get("followups", [])):
            step = row["followups"][bar - 2]
            assert step["operation"] == "set" and step["receiver"] in {"a", "b"}
            ref.matrix_set_v1(tx, a if step["receiver"] == "a" else b, *[argument(value) for value in step["arguments"]])

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
            "native_compile_meta": compiled.compile_meta,
            "generated_module_sha256": hashlib.sha256(compiled.python_code.encode()).hexdigest(),
            "artifact_hash": compiled.generated_artifact["content_hash"],
            **{field + "_sha256": case[field]["sha256"] for field in ("source", "data", "settings")}}


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_independent_matrix_shape_cells_scalar_and_alias(case, path, compact, observations):
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
    write_json(Path(output) / "builtin-matrix-operations-surface.json", surface)
    for (compact, path), values in rows.items():
        assert len(values) == 100
        assignments = []
        for case_id, value in values.items():
            assert len(value["executed_bindings"]) == 1
            row = indexed[tuple(value["executed_bindings"][0])]
            assert row["status"] == "RUNTIME_DIRECT"
            assignments.append({**{field: row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form", "contract_hash")},
                                "case_id": case_id, "path": path})
        assert len({tuple(row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form")) for row in assignments}) == 20
        report = builtin_evidence_report(surface, CORPUS, values, corpus_hash=LOCK["content_hash"], assignments=assignments)
        directory = Path(output) / "builtin-matrix-operations-reports" / ("compact" if compact else "full") / path
        write_json(directory / "observations.json", values)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "report.json", report)
        assert report["execution_evidence"]["all_assigned_passed"] and report["trace_comparison"]["ok"]
        assert not report["full_builtin_expected_accepted"]


def test_matrix_literal_expansion_and_primary_only_provenance_are_frozen():
    manual = CORPUS.parent / LOCK["manual_table"]["path"]
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == "8d5e61ff844ebb7c67f87f50f199b1621b08c1523cee9068ebb152c661370be9"
    assert len(MANIFEST["cases"]) == LOCK["case_count"] == 100
    table = read_json(manual)
    originals = {row["id"]: row for row in table["rows"] + table["reference_rows"]}
    assert len(originals) == LOCK["manual_case_count"] == 25
    keys = set()
    for case in MANIFEST["cases"]:
        settings = read_json(CORPUS.parent / case["settings"]["path"])
        assert typed_equal(settings["manual_row"], originals[settings["manual_case_id"]])
        assert settings["manual_table"] == LOCK["manual_table"] and case["oracle"]["kind"] == "manual_fixture"
        assert len(read_json(CORPUS.parent / case["expected"]["path"])["events"]) == 4
        keys.add((case["pine_version"], *settings["declared_binding"]))
    authority = CORPUS.parent / LOCK["primary_rule_review"]["path"]
    assert hashlib.sha256(authority.read_bytes()).hexdigest() == LOCK["primary_rule_review"]["sha256"]
    assert len(keys) == LOCK["versioned_tuple_count"] == 20
    assert MANIFEST["profile"] == "engineering" and LOCK["full_stage2_accepted"] is False


@pytest.mark.parametrize("left,right", [([False], [0]), ([1], [1.0]), ([1, 2], [2, 1]),
    ({"rows": 2, "columns": 3}, {"rows": 3, "columns": 2}),
    ({"rows": 0, "columns": 0}, {"rows": False, "columns": 0}),
    ({"same_id": True}, {"same_id": False}), ({"kind": "na"}, {"kind": "value", "value": None}),
    (["x"], [""]), ([1], [1, 2])])
def test_typed_matrix_observer_rejects_wrong_shape_cells_alias_and_missing_token(left, right):
    assert not typed_equal(left, right)
