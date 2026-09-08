"""Literal string results and separately retained authority gaps through real code."""
import ast
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib
import json
import math
import os
from pathlib import Path

import pytest
from ast2python import compile_consumer_bundle
from ast2python.lowering import load_pinelib_target_manifest
from pine2ast.hardening.consumer_bundle import build_consumer_bundle
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.runtime.metadata import BarValues

from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-string-operations-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
PATHS = ("abi", "compiled_historical", "compiled_realtime", "compiled_rollback", "compiled_checkpoint")
UNVERIFIED_DIFFERENCES = {"manual-tonumber-7-v5", "manual-tonumber-8-v5", "manual-tonumber-9-v5"}
AUTHORITY_PATH = CORPUS.parent / "string-v5-case-authority-independent-review.json"
AUTHORITY_SHA256 = "e21bf08a0f41341edcc47c8c5d70ab4c8ac9ef73150d3969c91d7aaea3010f6b"
assert hashlib.sha256(AUTHORITY_PATH.read_bytes()).hexdigest() == AUTHORITY_SHA256
AUTHORITY = read_json(AUTHORITY_PATH)
V5_AUTHORITY = {row["manual_id"]: row for row in AUTHORITY["rows"]}
UNVERIFIED_AUTHORITY = {f"manual-tonumber-{index}-v5" for index in range(7, 12)}
assert MANIFEST["content_hash"] == LOCK["content_hash"]


def source_identity():
    """The normal CI layer independently verifies these declared component pins."""
    pins = read_json(ROOT / "docs/RC6_LIFECYCLE_SOURCES.json")
    return {"mode": "declared_dependency_pins", **{name: pins[name] for name in ("pine2ast", "ast2python", "pinelib")}}


def typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(typed_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def value_record(value):
    if value is na:
        return {"kind": "na"}
    assert type(value) in {str, int, float}
    if type(value) is float:
        assert math.isfinite(value)
    return {"kind": "value", "value": value}


def declaration_storage(compiled):
    """Locate real scalar bindings by admitted declaration and generated source map."""
    declarations = {node.source.node_id: node.attributes["fields"]["name"] for node in compiled.plan.nodes.values()
                    if node.attributes.get("ast_kind") == "VarDeclaration" and node.attributes.get("scope_id") == "scope:global"
                    and node.attributes["fields"]["name"] in {"argument", "result"}}
    assert set(declarations.values()) == {"argument", "result"}
    calls = [node for node in ast.walk(ast.parse(compiled.emitted.code)) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "declare_scalar_v1"]
    storage = {}
    for node_id, name in declarations.items():
        spans = [(entry.python_start.line, entry.python_end.line) for entry in compiled.emitted.source_map.entries if entry.source_node_id == node_id]
        selected = [call for call in calls if any(start <= call.lineno <= end for start, end in spans)]
        assert len(selected) == 1 and isinstance(selected[0].args[0], ast.Constant)
        assert type(selected[0].args[0].value) is str
        storage[name] = selected[0].args[0].value
    assert len(set(storage.values())) == 2
    return storage


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(row for row in MANIFEST["cases"] if row["id"] == case_id)
    settings = read_json(CORPUS.parent / case["settings"]["path"])
    source = (CORPUS.parent / case["source"]["path"]).read_text(encoding="utf-8")
    identity = source_identity()
    bundle = build_consumer_bundle(source, source_name=settings["source_name"], producer_commit=identity["pine2ast"])
    literal = [row for row in bundle["semantic_facts"]["facts"] if row["kind"] == "Literal" and row["span"]["start_line"] == 3]
    assert len(literal) == 1
    if settings["argument"]["kind"] == "na":
        assert literal[0]["resolved_type"]["base"] == "na" and literal[0]["const_value"] is None
    else:
        assert type(literal[0]["const_value"]) is str and literal[0]["const_value"] == settings["argument"]["value"]
    nodes = {node["node_id"] for node in bundle["node_index"] if node["kind"] == "CallExpr" and node["span"]["start_line"] == settings["primary_source_line"]}
    calls = [row for row in bundle["semantic_facts"]["calls"] if row["node_id"] in nodes]
    assert len(calls) == 1
    key = tuple(calls[0][field] for field in ("symbol_id", "overload_id", "call_form"))
    assert key == tuple(settings["declared_binding"])
    assert calls[0]["return_type"] == settings["storage"]["result"]
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert binding.python_module + "." + binding.python_name == settings["abi_callable"]
    compiled = compile_consumer_bundle(bundle, target=target, expected_pine2ast_commit=identity["pine2ast"], producer_commit=identity["ast2python"])
    namespace = {}
    exec(compile(compiled.emitted.code, settings["source_name"] + ".py", "exec"), namespace)
    function = getattr(importlib.import_module(binding.python_module), binding.python_name)
    return settings, bundle, key, target, binding, compiled, namespace, function, declaration_storage(compiled), identity


def authority_for(settings):
    # Original v5 inference is an independent dimension from numerical agreement.
    if settings["initial_v5_inference"]:
        row = V5_AUTHORITY[settings["row_id"]]
        assert settings["version"] == row["pine_version"] == 5
        assert settings["function"] == row["function"]
        assert typed_equal(settings["argument"], {"kind": "value", "value": row["argument"]})
        assert typed_equal(settings["expected"], row["original_expected"])
        assert settings["original_authority"] == row["original_authority_label"]
        assert type(row["assignment_authority_eligible"]) is bool
        return {"classification": row["authority_status"], "confirmed": row["assignment_authority_eligible"],
                "original_label": settings["original_authority"], "independent_receipt_sha256": AUTHORITY_SHA256,
                "independent_receipt_row": row["manual_id"]}
    return {"classification": "PRIMARY_LITERAL_RULE", "confirmed": True,
            "original_label": settings["original_authority"]}


def validate_authority_gap_ledger(ledger, values):
    """Do not let matching values or edited labels manufacture source authority."""
    assert type(ledger) is dict and set(ledger) == {"schema_id", "rows", "original_v5_inference_cases", "full_stage2_accepted"}
    assert ledger["schema_id"] == "openpine.string_authority_gaps.v1"
    assert type(ledger["original_v5_inference_cases"]) is int and ledger["original_v5_inference_cases"] == 29
    assert ledger["full_stage2_accepted"] is False and type(ledger["rows"]) is list
    cases = {case["id"]: case for case in MANIFEST["cases"]}
    wanted = {}
    for case_id, value in values.items():
        settings = read_json(CORPUS.parent / cases[case_id]["settings"]["path"])
        authority = authority_for(settings)
        assert type(value["semantic_authority"]["confirmed"]) is bool and typed_equal(value["semantic_authority"], authority)
        if not authority["confirmed"]:
            wanted[case_id] = authority
    found = set()
    for row in ledger["rows"]:
        assert type(row) is dict and set(row) == {"case_id", "binding", "authority", "numerical_comparison", "assigned"}
        case_id = row["case_id"]
        assert type(case_id) is str and case_id in wanted and case_id not in found
        found.add(case_id)
        assert row["assigned"] is False and typed_equal(row["authority"], wanted[case_id])
        assert type(row["authority"]["confirmed"]) is bool and row["authority"]["confirmed"] is False
        assert typed_equal(row["binding"], values[case_id]["executed_bindings"][0])
        comparison = row["numerical_comparison"]
        assert type(comparison) is dict and set(comparison) == {"matched", "original_expected", "status"}
        assert type(comparison["matched"]) is bool
        expected = read_json(CORPUS.parent / cases[case_id]["expected"]["path"])
        assert typed_equal(comparison["original_expected"], expected)
        matched = typed_equal(expected, {"compile": values[case_id]["compile"], "events": values[case_id]["events"]})
        assert comparison["matched"] is matched
        assert comparison["status"] == ("MATCH" if matched else "UNVERIFIED_DIFFERENCE")
    assert found == wanted.keys()


def execute_case(case, path, compact):
    settings, bundle, key, target, binding, compiled, namespace, function, storage, identity = prepare(case["id"])
    argument = na if settings["argument"]["kind"] == "na" else settings["argument"]["value"]

    def make_runtime():
        runtime = RuntimeSession(RuntimeLanguageContext(case["pine_version"], "string-manual", f"pine-v{case['pine_version']}",
                                                       compiled.plan.source_hash, "compiler_annotation"))
        runtime.commit_full_identity = not compact
        return runtime

    runtime = make_runtime()
    sequence = 0
    trials, restores = [], []

    def snapshot():
        assert typed_equal(value_record(runtime.series[storage["argument"]].read()), settings["argument"])
        return value_record(runtime.series[storage["result"]].read())

    def abi_program(tx, bar):
        source = tx.declare_scalar_v1(storage["argument"], "var", lambda: argument, "string")
        tx.declare_scalar_v1(storage["result"], "var", lambda: settings["initial_result"], settings["storage"]["result"])
        if bar == 1:
            kwargs = {}
            source_count = 0
            for item in binding.parameter_bindings:
                if item["binding"] == "INJECTED":
                    assert item["source"] == "RUNTIME_TRANSACTION"
                    kwargs[item["abi_parameter"]] = tx
                else:
                    assert item["binding"] == "SOURCE_PARAMETER"
                    kwargs[item["abi_parameter"]] = source
                    source_count += 1
            assert source_count == 1
            result = function(**kwargs)
            tx.write_scalar_v1(storage["result"], "var", result, settings["storage"]["result"])

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
        value = snapshot()
        if trial and path == "compiled_rollback":
            tx.abort()
        else:
            tx.commit()
            sequence += 1
        return value

    def restore_json(bar, reason):
        nonlocal runtime
        before = snapshot()
        saved = runtime.checkpoint().to_dict()
        clone = make_runtime()
        clone.restore(json.loads(json.dumps(saved)))
        assert clone.checkpoint().to_dict() == saved
        runtime = clone
        assert typed_equal(snapshot(), before)
        restores.append({"bar": bar, "reason": reason, "sequence": runtime.sequence, "typed_values_preserved": True})

    events = []
    for bar in read_json(CORPUS.parent / case["data"]["path"]):
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            baseline = snapshot()
            transcript = deepcopy(runtime.transcript.to_dict())
            previous_sequence, attempted = runtime.sequence, sequence
            trial = callback(bar, True)
            trials.append({"bar": bar, "value": trial, "attempt_sequence": attempted})
            if path == "compiled_rollback":
                assert runtime.sequence == previous_sequence and sequence == attempted
                assert runtime.transcript.to_dict() == transcript and typed_equal(snapshot(), baseline)
                restore_json(bar, "aborted_same_sequence_retry")
        value = callback(bar)
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            assert typed_equal(trial, value)
        events.append({"bar": bar, "value": value})
        if path == "compiled_checkpoint" and bar == 1:
            restore_json(bar, "after_operation_before_continuation")
    return {"status": "completed", "compile": True, "events": events, "execution_path": path,
            "transcript_mode": "compact" if compact else "full", "trials": trials, "restores": restores,
            "executed_bindings": [[case["pine_version"], *key]], "target_manifest_hash": target.content_hash,
            "catalog_hash": bundle["version_context"]["catalog_hash"], "source_identity": identity,
            "semantic_authority": authority_for(settings), "original_v5_inference": settings["initial_v5_inference"],
            "generated_module_sha256": hashlib.sha256(compiled.emitted.code.encode()).hexdigest(),
            "artifact_hash": compiled.artifact.payload["content_hash"],
            **{field + "_sha256": case[field]["sha256"] for field in ("source", "data", "settings")}}


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_literal_string_value_or_explicit_unverified_observation(case, path, compact, observations):
    actual = execute_case(case, path, compact)
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    projection = {"compile": actual["compile"], "events": actual["events"]}
    matches = typed_equal(expected, projection)
    actual["numerical_comparison"] = {"matched": matches, "original_expected": expected,
                                      "status": "MATCH" if matches else "UNVERIFIED_DIFFERENCE"}
    if case["id"] in UNVERIFIED_DIFFERENCES:
        assert actual["original_v5_inference"] and actual["semantic_authority"]["confirmed"] is False
        assert expected["events"][1]["value"] == {"kind": "na"}
        # Record the actual typed result without declaring the old inference or
        # current compatibility value to be a confirmed Pine result.
        assert all(event["value"]["kind"] == "na" or type(event["value"]["value"]) is float for event in actual["events"][1:])
    else:
        assert first_difference(expected, projection, case["tolerance"]) is None
        assert matches
    observations.setdefault((compact, path), {})[case["id"]] = actual


@pytest.fixture(scope="module")
def observations():
    rows = {}
    yield rows
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):
        return
    surface = build_builtin_surface()
    indexed = {(row["pine_version"], row["symbol_id"], row["overload_id"], row["call_form"]): row for row in surface["rows"]}
    write_json(Path(output) / "builtin-string-operations-surface.json", surface)
    for (compact, path), values in rows.items():
        assert len(values) == 83
        assignments, gaps = [], []
        for case_id, value in values.items():
            assert len(value["executed_bindings"]) == 1
            key = value["executed_bindings"][0]
            row = indexed[tuple(key)]
            assert row["status"] == "RUNTIME_DIRECT"
            if not value["semantic_authority"]["confirmed"]:
                gaps.append({"case_id": case_id, "binding": key, "authority": value["semantic_authority"],
                             "numerical_comparison": value["numerical_comparison"], "assigned": False})
                continue
            assignments.append({**{field: row[field] for field in ("pine_version", "symbol_id", "overload_id", "call_form", "contract_hash")},
                                "case_id": case_id, "path": path})
        report = builtin_evidence_report(surface, CORPUS, values, corpus_hash=LOCK["content_hash"], assignments=assignments)
        ledger = {"schema_id": "openpine.string_authority_gaps.v1", "rows": gaps,
                  "original_v5_inference_cases": 29, "full_stage2_accepted": False}
        validate_authority_gap_ledger(ledger, values)
        assert len(assignments) == 78 and len(gaps) == 5
        assert {row["case_id"] for row in gaps} == UNVERIFIED_AUTHORITY
        assert all(row["numerical_comparison"]["matched"] for case_id, row in values.items() if case_id not in UNVERIFIED_DIFFERENCES)
        source_contracts = [{"pine_version": version, "symbol_id": "pine:function:str.tonumber", "reference_parameter_names": ["string"],
                             "catalog_parameter_names": [item["name"] for item in indexed[(version, "pine:function:str.tonumber", "pine:function:str.tonumber#canonical", "NAMESPACE_FUNCTION")]["contract"]["parameters"]],
                             "observed_call_scope": "one_positional_argument", "named_argument_behavior_covered": False}
                            for version in (5, 6)]
        directory = Path(output) / "builtin-string-operations-reports" / ("compact" if compact else "full") / path
        write_json(directory / "observations.json", values)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "unverified-authority-observations.json", ledger)
        write_json(directory / "source-contract-scope.json", {"schema_id": "openpine.string_positional_scope.v1", "rows": source_contracts,
                                                              "full_stage2_accepted": False})
        write_json(directory / "report.json", report)
        assert report["execution_evidence"]["all_assigned_passed"]
        assert not report["full_builtin_expected_accepted"]


def test_original_literals_and_all_v5_inference_labels_are_preserved():
    assert hashlib.sha256((CORPUS.parent / LOCK["manual_table"]["path"]).read_bytes()).hexdigest() == "67362aaa9d69bdb9297fbf862a870f4218cfef8493602936f2dca4522df728ac"
    assert hashlib.sha256((CORPUS.parent / LOCK["supplement"]["path"]).read_bytes()).hexdigest() == "7163768f5dacd31dc66a99971d7c9a60e535a1cafddd96c2213c8228c5a020f0"
    assert len(MANIFEST["cases"]) == LOCK["case_count"] == 83
    settings = [read_json(CORPUS.parent / case["settings"]["path"]) for case in MANIFEST["cases"]]
    assert sum(row["initial_v5_inference"] for row in settings) == LOCK["original_v5_inference_cases"] == 29
    assert {row["id"] for row in settings if row["unverified_numeric_disagreement"]} == UNVERIFIED_DIFFERENCES
    assert LOCK["legacy_native_controls_not_promoted"] == 8 and LOCK["full_stage2_accepted"] is False
    assert len(AUTHORITY["rows"]) == len(V5_AUTHORITY) == 29
    assert AUTHORITY["manual"]["sha256"] == LOCK["manual_table"]["sha256"]
    assert sum(authority_for(row)["confirmed"] for row in settings) == 78
    assert {row["id"] for row in settings if not authority_for(row)["confirmed"]} == UNVERIFIED_AUTHORITY
    assert {row["id"] for row in settings if row["initial_v5_inference"]} == {f"manual-{row_id}-v5" for row_id in V5_AUTHORITY}


@pytest.mark.parametrize("left,right", [(False, 0), (0, 0.0), (" A", "A"), ("É", "é"),
                                       ({"kind": "na"}, {"kind": "value", "value": None}), ("1\n", "1")])
def test_typed_observer_rejects_wrong_values_or_missing_token(left, right):
    assert not typed_equal(left, right)


@pytest.mark.parametrize("fault", ["unknown_case", "duplicate", "extra", "confirmed", "lost_origin", "numeric_bool",
                                   "numeric_status", "rewritten_expected", "assigned", "count_bool", "binding"])
def test_authority_ledger_rejects_forged_labels_and_numerical_proof(fault):
    values, rows = {}, []
    for case in MANIFEST["cases"]:
        settings = read_json(CORPUS.parent / case["settings"]["path"])
        authority = authority_for(settings)
        if authority["confirmed"]:
            continue
        expected = read_json(CORPUS.parent / case["expected"]["path"])
        binding = [case["pine_version"], *settings["declared_binding"]]
        # Synthetic agreement tests the metadata rule: numerical agreement alone
        # must not convert an unresolved original inference into authority.
        values[case["id"]] = {**deepcopy(expected), "semantic_authority": authority, "executed_bindings": [binding]}
        rows.append({"case_id": case["id"], "binding": binding, "authority": deepcopy(authority), "assigned": False,
                     "numerical_comparison": {"matched": True, "status": "MATCH", "original_expected": deepcopy(expected)}})
    ledger = {"schema_id": "openpine.string_authority_gaps.v1", "rows": rows, "original_v5_inference_cases": 29, "full_stage2_accepted": False}
    assert len(rows) == 5
    validate_authority_gap_ledger(ledger, values)
    row = rows[0]
    if fault == "unknown_case":
        row["case_id"] = "unknown"
    elif fault == "duplicate":
        rows[1] = deepcopy(row)
    elif fault == "extra":
        row["hidden"] = True
    elif fault == "confirmed":
        row["authority"]["confirmed"] = True
    elif fault == "lost_origin":
        row["authority"].pop("original_label")
    elif fault == "numeric_bool":
        row["numerical_comparison"]["matched"] = 1
    elif fault == "numeric_status":
        row["numerical_comparison"]["status"] = "UNVERIFIED_DIFFERENCE"
    elif fault == "rewritten_expected":
        row["numerical_comparison"]["original_expected"]["events"][1]["value"] = {"kind": "value", "value": "rewritten"}
    elif fault == "assigned":
        row["assigned"] = True
    elif fault == "count_bool":
        ledger["original_v5_inference_cases"] = True
    else:
        row["binding"] = [6, *row["binding"][1:]]
    with pytest.raises(AssertionError):
        validate_authority_gap_ledger(ledger, values)
