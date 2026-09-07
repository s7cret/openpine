"""Independent builtin tables through real ABI and generated Pine execution.

Expected files are authored mathematical examples, never extracted from the
runtime. These cases add evidence without accepting untested catalogue rows.
"""

import json
import os
from importlib import import_module
from functools import lru_cache
from pathlib import Path

import pytest
from ast2python import compile_consumer_bundle
from ast2python.lowering import load_pinelib_target_manifest
from pine2ast.hardening.consumer_bundle import build_consumer_bundle
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, is_na, na
from pinelib.input import InputRegistry
from pinelib.runtime.metadata import BarValues
from pinelib.state.checkpoint import from_portable

from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import compare_corpus, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtins-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")["content_hash"]
PATHS = (
    "abi",
    "compiled_historical",
    "compiled_realtime",
    "compiled_rollback",
    "compiled_checkpoint",
)


def _session(version, metadata):
    return RuntimeSession(
        RuntimeLanguageContext(
            version,
            "builtin-manual-fixtures",
            f"pine-v{version}",
            "sha256:" + "a" * 64,
            "compiler_annotation",
        ),
        inputs=InputRegistry.from_descriptors(metadata.get("inputs", {})),
    )


@lru_cache(maxsize=None)
def _case_manifest(corpus_path):
    # All files are checked before the corpus's first execution. Final evidence
    # reports revalidate the whole lock, as for the original fixed corpus.
    return MANIFEST if corpus_path == CORPUS else load_corpus(corpus_path)


def _fixture_argument(value, close):
    # The engineering corpus uses its own visible NA marker. Convert that
    # exact fixture token to PineLib's canonical value at the runner boundary;
    # it is not the runtime checkpoint transport ({"$pine": "na"}).
    if type(value) is dict and set(value) == {"$na"} and value["$na"] is True:
        return na
    return close if value == "close" else from_portable(value)


@lru_cache(maxsize=None)
def _prepare_case(case_id, corpus_path=CORPUS):
    # One immutable compiled artifact is reused across five independent sessions.
    # This also detects accidental generated-class state leaking between paths.
    manifest = _case_manifest(corpus_path)
    case = next(row for row in manifest["cases"] if row["id"] == case_id)
    source = (corpus_path.parent / case["source"]["path"]).read_text()
    settings = read_json(corpus_path.parent / case["settings"]["path"])
    closes = read_json(corpus_path.parent / case["data"]["path"])
    bundle = build_consumer_bundle(
        source, source_name=settings["source_name"], producer_commit="1" * 40
    )
    call = next(c for c in bundle["semantic_facts"]["calls"] if c["callee"] == settings["spelling"])
    key = (call["symbol_id"], call["overload_id"], call["call_form"])
    if "declared_binding" in settings:
        assert list(key) == settings["declared_binding"]
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case["pine_version"] in binding.supported_pine_versions
    assert f"{binding.python_module}.{binding.python_name}" == settings["abi_callable"]
    compiled = compile_consumer_bundle(
        bundle, target=target, expected_pine2ast_commit="1" * 40, producer_commit="2" * 40
    )
    namespace = {}
    exec(compile(compiled.emitted.code, settings["source_name"] + ".py", "exec"), namespace)
    metadata = namespace["SCRIPT_METADATA"]
    return settings, closes, bundle, key, target, namespace, metadata


def execute_case(case, path, *, corpus_path=CORPUS):
    settings, closes, bundle, key, target, namespace, metadata = _prepare_case(
        case["id"], corpus_path
    )
    runtime = _session(case["pine_version"], metadata)
    module, name = settings["abi_callable"].rsplit(".", 1)
    function = getattr(import_module(module), name)
    sequence = 0

    def callback(bar, close, *, trial=False):
        nonlocal sequence
        tx = runtime.begin(
            CallbackFrame(
                "REALTIME_TICK"
                if path in {"compiled_realtime", "compiled_rollback"}
                else "HISTORICAL_EVAL",
                sequence,
                bar_index=bar,
                realtime=path in {"compiled_realtime", "compiled_rollback"},
                final_tick=not trial,
            ),
            values=BarValues(
                close, close + 1, close - 1, close, 1, bar * 60000, (bar + 1) * 60000 - 1
            ),
        )
        sequence += 1
        if path == "abi":
            arguments = (
                settings["arguments_by_bar"][bar]
                if "arguments_by_bar" in settings else settings["arguments"]
            )
            args = [_fixture_argument(value, close) for value in arguments]
            injections = settings.get(
                "abi_injections", ["TX", "STATE_KEY"] if settings["stateful"] else []
            )
            assert all(item in {"TX", "STATE_KEY"} for item in injections)
            injected = {"TX": tx, "STATE_KEY": "independent-builtin"}
            value = function(*(injected[item] for item in injections), *args)
            if "abi_result_type" in settings and not is_na(value):
                assert type(value) is {"int": int, "float": float, "bool": bool}[
                    settings["abi_result_type"]
                ]
            if settings.get("result_projection") == "bool_to_01":
                assert type(value) is bool
                value = 1 if value is True else 0
        else:
            namespace["GeneratedScript"](tx).run()
            value = from_portable(runtime.visuals.working[-1].payload["series"])
        if trial and path == "compiled_rollback":
            tx.abort()
        else:
            tx.commit()
        return {"$na": True} if is_na(value) else value

    events = []
    for index, close in enumerate(closes):
        if path in {"compiled_realtime", "compiled_rollback"}:
            callback(index, close + 100, trial=True)
        events.append({"bar": index, "value": callback(index, close)})
        if path == "compiled_checkpoint" and index == 1:
            checkpoint = json.loads(json.dumps(runtime.checkpoint().to_dict()))
            runtime = _session(case["pine_version"], metadata)
            runtime.restore(checkpoint)
    return {
        "status": "completed",
        "compile": True,
        "events": events,
        "execution_path": path,
        "executed_bindings": [[case["pine_version"], *key]],
        "target_manifest_hash": target.content_hash,
        "catalog_hash": bundle["version_context"]["catalog_hash"],
        **{k + "_sha256": case[k]["sha256"] for k in ("source", "data", "settings")},
    }


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c: c["id"])
@pytest.mark.parametrize("path", PATHS)
def test_independent_builtin_expected(case, path, builtin_observations):
    observed = execute_case(case, path)
    builtin_observations.setdefault(path, {})[case["id"]] = observed
    report = compare_corpus(CORPUS, {case["id"]: observed}, expected_corpus_hash=LOCK)
    own = next(row for row in report["results"] if row["id"] == case["id"])
    assert own["status"] == "PASS", own
    assert not report["tradingview_verified"]
    if output := os.environ.get("OPENPINE_STAGE1_EVIDENCE"):
        write_json(Path(output) / "builtin-observations" / path / (case["id"] + ".json"), observed)


@pytest.fixture(scope="module")
def builtin_observations():
    observations = {}
    yield observations
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):
        return
    surface = build_builtin_surface()
    indexed = {
        (r["pine_version"], r["symbol_id"], r["overload_id"], r["call_form"]): r
        for r in surface["rows"]
    }
    write_json(Path(output) / "builtin-surface.json", surface)
    for path, observed in observations.items():
        assignments = []
        for case_id, observation in observed.items():
            for binding in observation["executed_bindings"]:
                row = indexed[tuple(binding)]
                assignments.append(
                    {
                        **{
                            k: row[k]
                            for k in (
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
            surface, CORPUS, observed, corpus_hash=LOCK, assignments=assignments
        )
        directory = Path(output) / "builtin-reports" / path
        write_json(directory / "observations.json", observed)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "report.json", report)


def test_builtin_surface_retains_every_version_and_unverified_signature():
    surface = build_builtin_surface()
    assert {row["pine_version"] for row in surface["rows"]} == set(range(1, 7))
    assert len(surface["rows"]) > 2000
    assert all(row["oracle"] == "missing" for row in surface["rows"])
    report = builtin_evidence_report(surface, CORPUS, {}, corpus_hash=LOCK, assignments=[])
    assert report["denominator"] == len(surface["rows"])
    assert report["signatures_with_passing_examples"] == 0
    assert report["signatures_without_passing_examples"] == len(surface["rows"])
    assert not report["execution_evidence"]["all_assigned_passed"]
    assert not report["full_builtin_expected_accepted"]


def test_matching_execution_identity_passes_only_its_assigned_example():
    case = next(c for c in MANIFEST["cases"] if c["id"] == "v6-sqrt-four")
    observed = execute_case(case, "compiled_historical")
    surface = build_builtin_surface()
    row = next(
        r
        for r in surface["rows"]
        if [r[k] for k in ("pine_version", "symbol_id", "overload_id", "call_form")]
        == observed["executed_bindings"][0]
    )
    assignment = {
        k: row[k]
        for k in ("pine_version", "symbol_id", "overload_id", "call_form", "contract_hash")
    }
    assignment.update(case_id=case["id"], path="compiled_historical")
    report = builtin_evidence_report(
        surface, CORPUS, {case["id"]: observed}, corpus_hash=LOCK, assignments=[assignment]
    )
    assert report["execution_evidence"] == {
        "scope": "assigned_case_binding_paths",
        "assigned": 1,
        "passed": 1,
        "failed": 0,
        "all_assigned_passed": True,
    }
    assert report["signatures_with_passing_examples"] == 1
    assert report["signatures_without_passing_examples"] == len(surface["rows"]) - 1
    assert not report["full_builtin_expected_accepted"]


@pytest.mark.parametrize(
    "fault", ["path", "binding", "version", "contract", "duplicate", "target", "catalog"]
)
def test_independent_expected_cannot_be_relabelled_as_another_execution(fault):
    case = next(c for c in MANIFEST["cases"] if c["id"] == "v6-sqrt-four")
    observed = execute_case(case, "compiled_historical")
    surface = build_builtin_surface()
    key = observed["executed_bindings"][0]
    row = next(
        r
        for r in surface["rows"]
        if [r[k] for k in ("pine_version", "symbol_id", "overload_id", "call_form")] == key
    )
    assignment = {
        k: row[k]
        for k in ("pine_version", "symbol_id", "overload_id", "call_form", "contract_hash")
    }
    assignment.update(case_id=case["id"], path="compiled_historical")
    assignments = [assignment]
    if fault == "path":
        assignment["path"] = "protected_worker"
    elif fault == "binding":
        observed["executed_bindings"] = []
    elif fault in {"target", "catalog"}:
        observed["target_manifest_hash" if fault == "target" else "catalog_hash"] = (
            "sha256:" + "0" * 64
        )
    elif fault == "version":
        assignment["pine_version"] = 5
    elif fault == "contract":
        assignment["contract_hash"] = "sha256:" + "0" * 64
    else:
        assignments.append(dict(assignment))
    if fault in {"version", "contract", "duplicate"}:
        with pytest.raises(ValueError):
            builtin_evidence_report(
                surface, CORPUS, {case["id"]: observed}, corpus_hash=LOCK, assignments=assignments
            )
    else:
        report = builtin_evidence_report(
            surface, CORPUS, {case["id"]: observed}, corpus_hash=LOCK, assignments=assignments
        )
        assert report["signatures_with_passing_examples"] == 0
        assert report["execution_evidence"]["passed"] == 0
        assert report["execution_evidence"]["failed"] == 1
        assert not report["execution_evidence"]["all_assigned_passed"]
        numeric = next(r for r in report["trace_comparison"]["results"] if r["id"] == case["id"])
        assert numeric["status"] == "PASS"
        evidence = next(r["evidence"] for r in report["rows"] if r["evidence"])
        assert evidence[0]["result"]["status"] == "EXECUTION_PATH_OR_BINDING_MISMATCH"


@pytest.mark.parametrize("fault", ["unbound", "version", "missing", "callable"])
def test_builtin_surface_does_not_report_incomplete_binding_as_direct(fault):
    from dataclasses import replace

    target = load_pinelib_target_manifest()
    key = ("pine:function:math.sqrt", "pine:function:math.sqrt#canonical", "NAMESPACE_FUNCTION")
    original = target.call_bindings[key]
    bindings = dict(target.call_bindings)
    if fault == "unbound":
        bindings[key] = replace(
            original,
            parameter_bindings=(
                {"binding": "UNBOUND_FAIL_CLOSED", "abi_parameter": "value", "source": None},
            ),
        )
    elif fault == "version":
        bindings[key] = replace(original, supported_pine_versions=(5,))
    elif fault == "missing":
        bindings.pop(key)
    else:
        bindings[key] = replace(original, python_name="missing_builtin_for_negative_test")
    surface = build_builtin_surface(target=replace(target, call_bindings=bindings))
    row = next(
        r
        for r in surface["rows"]
        if r["pine_version"] == 6 and (r["symbol_id"], r["overload_id"], r["call_form"]) == key
    )
    assert row["status"] == "UNAVAILABLE"
    assert row["reasons"]
