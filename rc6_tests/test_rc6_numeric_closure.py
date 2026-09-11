"""New independent trajectories for residual numeric signature rows.

Expected files come from a stdlib-only rational derivation, not from this runner.
The explicit TSI observation horizon is retained as a limitation in every record.
"""

from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest
from ast2python.lowering import load_pinelib_target_manifest
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.abi import reference
from pinelib.runtime.metadata import BarValues
from pinelib.state.checkpoint import from_portable
from pinelib.ta.types import MacdResult

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.evidence_index import EXECUTION_PATHS
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-numeric-closure-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
assert MANIFEST["content_hash"] == LOCK["content_hash"]
assert len(MANIFEST["cases"]) == LOCK["case_count"] == 58


def record(value):
    if value is na:
        return {"kind": "na"}
    if type(value) is MacdResult:
        return [record(value.macd), record(value.signal), record(value.histogram)]
    if type(value) in (tuple, list):
        return [record(item) for item in value]
    assert type(value) in (int, float) and math.isfinite(value)
    return {"kind": "value", "value": value}


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(row for row in MANIFEST["cases"] if row["id"] == case_id)
    settings = read_json(CORPUS.parent / case["settings"]["path"])
    source = (CORPUS.parent / case["source"]["path"]).read_text()
    pins = read_json(ROOT / "docs/RC6_LIFECYCLE_SOURCES.json")
    identity = {
        "mode": "declared_dependency_pins",
        **{name: pins[name] for name in ("pine2ast", "ast2python", "pinelib")},
    }
    compiled = NativeRC6CompilerAdapter().compile(
        source,
        source_name=settings["source_name"],
        producer_commits={name: pins[name] for name in ("pine2ast", "ast2python")},
    )
    assert compiled.success, compiled.errors
    bundle = compiled.consumer_bundle
    nodes = {
        row["node_id"]
        for row in bundle["node_index"]
        if row["kind"] == "CallExpr"
        and row["span"]["start_line"] == settings["primary_source_line"]
    }
    calls = [row for row in bundle["semantic_facts"]["calls"] if row["node_id"] in nodes]
    assert len(calls) == 1
    key = tuple(calls[0][field] for field in ("symbol_id", "overload_id", "call_form"))
    assert list(key) == settings["declared_binding"]
    expected_type = (
        "int"
        if settings["operation"].startswith("array.")
        else ("tuple<float,float,float>" if settings["arity"] == 3 else "float")
    )
    assert calls[0]["return_type"] == expected_type
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert binding.python_module + "." + binding.python_name == settings["abi_callable"]
    namespace = {}
    exec(compile(compiled.python_code, settings["source_name"] + ".py", "exec"), namespace)
    function = getattr(importlib.import_module(binding.python_module), binding.python_name)
    return settings, compiled, bundle, key, target, namespace, function, identity


def execute_case(case, path, compact):
    settings, compiled, bundle, key, target, namespace, function, identity = prepare(case["id"])
    data = read_json(CORPUS.parent / case["data"]["path"])
    prices = data["prices"]

    def make_runtime():
        runtime = RuntimeSession(
            RuntimeLanguageContext(
                case["pine_version"],
                "numeric-closure-manual",
                f"pine-v{case['pine_version']}",
                compiled.generated_artifact["source_hash"],
                "compiler_annotation",
            )
        )
        runtime.commit_full_identity = not compact
        return runtime

    runtime = make_runtime()
    sequence = 0
    trials, restores = [], []

    def restore(bar, reason):
        nonlocal runtime
        saved = runtime.checkpoint().to_dict()
        clone = make_runtime()
        clone.restore(json.loads(json.dumps(saved)))
        assert clone.checkpoint().to_dict() == saved
        runtime = clone
        restores.append({"bar": bar, "reason": reason, "whole_checkpoint_preserved": True})

    def callback(bar, trial=False):
        nonlocal sequence
        close = float(prices[bar]) + (0.25 if trial and path == "compiled_rollback" else 0.0)
        realtime = bar > 0 and path in {"compiled_realtime", "compiled_rollback"}
        tx = runtime.begin(
            CallbackFrame(
                "REALTIME_TICK" if realtime else "HISTORICAL_EVAL",
                sequence,
                bar_index=bar,
                realtime=realtime,
                final_tick=not trial,
            ),
            values=BarValues(
                close, close + 1, close - 1, close, 1, bar * 60000, (bar + 1) * 60000 - 1
            ),
        )
        if path == "abi":
            operation = settings["operation"]
            if operation.startswith("array."):
                handle = reference.array_new_v1(tx, f"abi:{bar}", "float")
                for number in data["numbers"]:
                    reference.array_push_v1(tx, handle, number)
                raw = function(tx, handle, close)
                assert type(raw) is int
            elif operation.startswith("math."):
                operands = [
                    na
                    if expression == "sample" and bar in settings["missing_bars"]
                    else close
                    if expression in {"close", "sample"}
                    else -close
                    if expression == "-close"
                    else float(expression)
                    for expression in data["operands"]
                ]
                raw = function(*operands)
            else:
                raw = function(tx, "independent-numeric-closure", close, *settings["parameters"])
            actual = record(raw)
        else:
            namespace["GeneratedScript"](tx).run()
            plots = runtime.visuals.working[-settings["arity"] :]
            assert len(plots) == settings["arity"]
            values = [record(from_portable(plot.payload["series"])) for plot in plots]
            actual = values if settings["arity"] == 3 else values[0]
        if trial and path == "compiled_rollback":
            tx.abort()
        else:
            tx.commit()
            sequence += 1
        return actual

    events = []
    for bar in range(len(prices)):
        if bar > 0 and path in {"compiled_realtime", "compiled_rollback"}:
            saved = deepcopy(runtime.checkpoint().to_dict())
            attempted = sequence
            trial = callback(bar, True)
            trials.append({"bar": bar, "value": trial})
            if path == "compiled_rollback":
                assert runtime.checkpoint().to_dict() == saved and sequence == attempted
                restore(bar, "aborted_same_sequence_retry")
        actual = callback(bar)
        if bar > 0 and path == "compiled_realtime":
            assert trial == actual
        if bar >= settings["observed_from_bar"]:
            events.append({"bar": bar, "value": actual})
        if path == "compiled_checkpoint":
            restore(bar, "committed_before_continuation")
    return {
        "status": "completed",
        "compile": True,
        "events": events,
        "execution_path": path,
        "transcript_mode": "compact" if compact else "full",
        "trials": trials,
        "restores": restores,
        "executed_bindings": [[case["pine_version"], *key]],
        "target_manifest_hash": target.content_hash,
        "catalog_hash": bundle["version_context"]["catalog_hash"],
        "source_identity": identity,
        "evidence_scope": settings["scope"],
        "observed_from_bar": settings["observed_from_bar"],
        "generated_module_sha256": hashlib.sha256(compiled.python_code.encode()).hexdigest(),
        "artifact_hash": compiled.generated_artifact["content_hash"],
        **{field + "_sha256": case[field]["sha256"] for field in ("source", "data", "settings")},
    }


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda row: row["id"])
@pytest.mark.parametrize("path", EXECUTION_PATHS)
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_independent_numeric_closure(case, path, compact, observations):
    actual = execute_case(case, path, compact)
    # Record attempted observations before asserting, so failure cannot disappear
    # from exported evidence as if the case was never attempted.
    observations.setdefault((compact, path), {})[case["id"]] = actual
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    assert (
        first_difference(
            expected, {"compile": actual["compile"], "events": actual["events"]}, case["tolerance"]
        )
        is None
    )


@pytest.fixture(scope="module")
def observations():
    rows = {}
    yield rows
    output = os.environ.get("OPENPINE_STAGE1_EVIDENCE")
    if not output:
        return
    surface = build_builtin_surface()
    indexed = {
        (r["pine_version"], r["symbol_id"], r["overload_id"], r["call_form"]): r
        for r in surface["rows"]
    }
    write_json(Path(output) / "builtin-numeric-closure-surface.json", surface)
    for (compact, path), values in rows.items():
        assignments = []
        for case_id, observed in values.items():
            row = indexed[tuple(observed["executed_bindings"][0])]
            assignments.append(
                {
                    **{
                        field: row[field]
                        for field in (
                            "pine_version",
                            "symbol_id",
                            "overload_id",
                            "call_form",
                            "contract_hash",
                        )
                    },
                    "case_id": case_id,
                    "path": path,
                }
            )
        report = builtin_evidence_report(
            surface, CORPUS, values, corpus_hash=LOCK["content_hash"], assignments=assignments
        )
        folder = (
            Path(output)
            / "builtin-numeric-closure-reports"
            / ("compact" if compact else "full")
            / path
        )
        write_json(folder / "observations.json", values)
        write_json(folder / "assignments.json", assignments)
        write_json(folder / "report.json", report)


def test_stdlib_derivation_reproduces_all_frozen_files():
    subprocess.run(  # noqa: S603 - fixed in-repository stdlib-only derivation, no user command
        [sys.executable, "-I", str(ROOT / "scripts/derive_numeric_closure.py"), "--check"],
        check=True,
    )


def test_historical_and_conditioned_limits_not_erased():
    assert {row["pine_version"] for row in MANIFEST["cases"]} == {3, 4, 5, 6}
    assert "v1_v2_rsi_macd_primary_bootstrap" in LOCK["unresolved"]
    assert "tsi_startup_missing_values" in LOCK["unresolved"]
    tsi = [row for row in MANIFEST["cases"] if row["id"].startswith("tsi-")]
    assert len(tsi) == 8
    for row in tsi:
        setting = read_json(CORPUS.parent / row["settings"]["path"])
        expected = read_json(CORPUS.parent / row["expected"]["path"])
        assert setting["observed_from_bar"] == 12
        assert expected["events"][0]["bar"] == 12
        assert "NOT_startup" in setting["scope"]
