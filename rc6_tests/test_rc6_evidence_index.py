"""Adversarial accounting tests: a PASS report alone is never an oracle."""

from copy import deepcopy

import pytest

from openpine.verification.builtins import builtin_evidence_report
from openpine.verification.evidence_index import (
    EXECUTION_PATHS,
    build_evidence_index,
    compare_surface_lock,
    make_surface_lock,
)
from openpine.verification.identity import canonical, digest, read_json, seal, write_json

PINS = {name: letter * 40 for name, letter in zip(("pine2ast", "ast2python", "pinelib"), "abc")}


def reseal(value):
    return seal({key: item for key, item in value.items() if key != "content_hash"})


def setup(root, *, legacy=False):
    host, evidence = root / "host", root / "run"
    host.mkdir(parents=True)
    evidence.mkdir()
    contract = {"parameters": [{"name": "number", "type": "float"}], "returns": "float"}
    row = {
        "pine_version": 6,
        "symbol_id": "pine:function:math.min",
        "overload_id": "pine:function:math.min#canonical",
        "call_form": "NAMESPACE_FUNCTION",
        "contract": contract,
        "contract_hash": digest(contract),
        "spellings": ["math.min"],
        "frontend_available": True,
        "status": "RUNTIME_DIRECT",
        "reasons": [],
        "target_binding": {},
        "qualifier_contract": {"status": "COMPATIBLE", "reasons": []},
        "oracle": "missing",
    }
    surface = seal(
        {
            "schema_id": "openpine.builtin_surface.v1",
            "catalogs": {"6": "catalog"},
            "target_manifest_hash": "target",
            "denominator_kind": "test_shape",
            "rows": [row],
        }
    )
    source = b'//@version=6\nindicator("fixture")\nplot(math.min(close,open))\n'
    data = {"close": 2.0, "open": 3.0}
    expected = {"compile": True, "events": [{"bar": 0, "value": 2.0}]}
    import hashlib

    def descriptor(name, value):
        path = host / name
        path.write_bytes(value if isinstance(value, bytes) else canonical(value))
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    case = {
        "id": "min",
        "pine_version": 6,
        "layer": "runtime",
        "critical": True,
        "weight": 1,
        "source": descriptor("case.pine", source),
        "data": descriptor("bars.json", data),
        "settings": descriptor("settings.json", {}),
        "expected": descriptor("expected.json", expected),
        "tolerance": {"absolute": 0, "relative": 0},
        "oracle": {"kind": "manual_fixture", "provenance": "The smaller of two numbers is 2."},
    }
    corpus = seal(
        {
            "schema_id": "openpine.conformance_corpus.v1",
            "revision": 1,
            "profile": "engineering",
            "cases": [case],
        }
    )
    write_json(host / "manifest.json", corpus)
    plan = seal(
        {
            "schema_id": "openpine.builtin_evidence_plan.v1",
            "groups": [
                {
                    "id": "min",
                    "corpus": "manifest.json",
                    "corpus_hash": corpus["content_hash"],
                    "report_root": "reports",
                    "variants": ["legacy"] if legacy else ["full", "compact"],
                    "paths": list(EXECUTION_PATHS),
                }
            ],
        }
    )
    write_json(evidence / "source-pins.json", PINS)
    for variant in plan["groups"][0]["variants"]:
        for path in EXECUTION_PATHS:
            folder = (
                evidence / "reports" / path if legacy else evidence / "reports" / variant / path
            )
            observed = {
                "status": "completed",
                **expected,
                "execution_path": path,
                "transcript_mode": variant,
                "catalog_hash": "catalog",
                "target_manifest_hash": "target",
                "executed_bindings": [[6, row["symbol_id"], row["overload_id"], row["call_form"]]],
                **{key + "_sha256": case[key]["sha256"] for key in ("source", "data", "settings")},
            }
            if not legacy:
                observed["source_identity"] = PINS
            assignment = {
                **{
                    key: row[key]
                    for key in (
                        "pine_version",
                        "symbol_id",
                        "overload_id",
                        "call_form",
                        "contract_hash",
                    )
                },
                "case_id": "min",
                "path": path,
            }
            observations = {"min": deepcopy(observed)}
            write_json(folder / "observations.json", observations)
            write_json(folder / "assignments.json", [assignment])
            write_json(
                folder / "report.json",
                builtin_evidence_report(
                    surface,
                    host / "manifest.json",
                    observations,
                    corpus_hash=corpus["content_hash"],
                    assignments=[assignment],
                ),
            )
    frozen = seal(
        {
            "schema_id": "openpine.builtin_assignment_lock.v1",
            "corpus_hash": corpus["content_hash"],
            "assignments": [{key: value for key, value in assignment.items() if key != "path"}],
        }
    )
    write_json(host / "assignments-lock.json", frozen)
    plan["groups"][0].update(
        assignment_lock="assignments-lock.json", assignment_hash=frozen["content_hash"]
    )
    plan = reseal(plan)
    return host, evidence, surface, make_surface_lock(surface), plan


def index(setup_result, roots=None):
    host, root, surface, lock, plan = setup_result
    return build_evidence_index(
        surface, lock, plan, host_root=host, evidence_roots=roots or [root], source_pins=PINS
    )


def rewrite_observed(fixture, field, value, *, fresh_report=True):
    host, root, surface, _, plan = fixture
    folder = root / "reports/full/abi"
    observations = read_json(folder / "observations.json")
    observations["min"][field] = value
    write_json(folder / "observations.json", observations)
    if fresh_report:
        report = builtin_evidence_report(
            surface,
            host / "manifest.json",
            observations,
            corpus_hash=plan["groups"][0]["corpus_hash"],
            assignments=read_json(folder / "assignments.json"),
        )
        write_json(folder / "report.json", report)


def test_all_paths_replayed_and_examples_are_not_stage_acceptance(tmp_path):
    fixture = setup(tmp_path)
    report = index(fixture)
    assert report["ok"] and report["passed_group_paths"] == 10
    assert report["direct_with_examples_all_paths"] == report["denominator"] == 1
    assert report["rows"][0]["unique_cases"] == 1
    assert report["rows"][0]["contract_fully_verified"] is False
    assert report["full_stage2_accepted"] is report["tradingview_verified"] is False


def test_duplicate_runs_do_not_inflate_denominator_case_or_input_counts(tmp_path):
    import shutil

    fixture = setup(tmp_path)
    root = fixture[1]
    second = tmp_path / "repeat"
    shutil.copytree(root, second)
    assert index(fixture, [root, root, second]) == index(fixture, [second, root]) == index(fixture)


def test_failed_retry_cannot_hide_behind_a_successful_run(tmp_path):
    import shutil

    fixture = setup(tmp_path)
    original = tmp_path / "passed"
    shutil.copytree(fixture[1], original)
    rewrite_observed(fixture, "events", [{"bar": 0, "value": 123.0}])
    report = index(fixture, [fixture[1], original])
    assert not report["ok"] and report["rows"][0]["evidence_status"] == "FAILED"
    assert report["direct_with_examples_all_paths"] == 0
    assert any(e["status"] == "CONFLICT" for e in report["rows"][0]["evidence"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("settings_sha256", "changed"),
        ("data_sha256", "changed"),
        ("source_sha256", "changed"),
        ("catalog_hash", "old"),
        ("target_manifest_hash", "old"),
        ("execution_path", "other"),
        ("executed_bindings", []),
        ("status", "timeout"),
        ("compile", False),
        ("events", [{"bar": 0, "value": False}]),
        ("events", []),
        ("transcript_mode", "compact"),
        ("source_identity", {**PINS, "pine2ast": "d" * 40}),
    ],
)
def test_identity_or_trace_failures_do_not_receive_fresh_credit(tmp_path, field, value):
    fixture = setup(tmp_path)
    rewrite_observed(fixture, field, value)
    report = index(fixture)
    assert not report["ok"] and report["direct_with_examples_all_paths"] == 0


def test_forged_pass_report_is_recomputed_not_trusted(tmp_path):
    fixture = setup(tmp_path)
    rewrite_observed(fixture, "events", [{"bar": 0, "value": 123.0}], fresh_report=False)
    report = index(fixture)
    assert not report["ok"]
    assert "REPORT_REPLAY_MISMATCH" in report["groups"][0]["reasons"]
    assert report["rows"][0]["evidence_status"] == "FAILED"


@pytest.mark.parametrize("name", ["observations", "assignments", "report"])
def test_partial_run_is_visible_not_silently_dropped(tmp_path, name):
    fixture = setup(tmp_path)
    (fixture[1] / f"reports/full/abi/{name}.json").unlink()
    report = index(fixture)
    assert not report["ok"] and report["required_group_paths"] == 10
    assert report["groups"][0]["status"] == "FAILED"


def test_no_runs_retains_entire_denominator(tmp_path):
    import shutil

    fixture = setup(tmp_path)
    shutil.rmtree(fixture[1] / "reports")
    report = index(fixture)
    assert not report["ok"] and report["denominator"] == 1
    assert report["counts"] == {"NO_EXAMPLES": 1}
    assert len(report["groups"]) == 10


def test_empty_assignment_is_not_a_passing_group(tmp_path):
    fixture = setup(tmp_path)
    folder = fixture[1] / "reports/full/abi"
    write_json(folder / "assignments.json", [])
    report = index(fixture)
    assert not report["ok"] and "EMPTY_OR_WRONG_PATH_ASSIGNMENTS" in report["groups"][0]["reasons"]


def test_legacy_run_requires_pinned_source_identity(tmp_path):
    fixture = setup(tmp_path, legacy=True)
    assert index(fixture)["ok"]
    (fixture[1] / "source-pins.json").unlink()
    report = index(fixture)
    assert not report["ok"] and report["direct_with_examples_all_paths"] == 0
    assert all("STALE_SOURCE" in group["reasons"] for group in report["groups"])


def test_stale_surface_report_is_never_joined_by_symbol_name(tmp_path):
    fixture = setup(tmp_path)
    folder = fixture[1] / "reports/full/abi"
    report = read_json(folder / "report.json")
    report["surface_hash"] = "old"
    write_json(folder / "report.json", reseal(report))
    result = index(fixture)
    assert not result["ok"] and result["groups"][0]["reasons"] == ["STALE_SURFACE"]


@pytest.mark.parametrize("change", ["remove", "add", "contract", "catalog", "kind"])
def test_changed_surface_requires_explicit_rebaseline(tmp_path, change):
    fixture = setup(tmp_path)
    surface, lock = deepcopy(fixture[2]), fixture[3]
    if change == "remove":
        # Empty surface is invalid before any percentage can be computed.
        surface["rows"] = []
        with pytest.raises(ValueError, match="empty"):
            compare_surface_lock(reseal(surface), lock)
        return
    if change == "add":
        row = deepcopy(surface["rows"][0])
        row["call_form"] = "METHOD"
        surface["rows"].append(row)
    elif change == "contract":
        surface["rows"][0]["contract"]["returns"] = "int"
        surface["rows"][0]["contract_hash"] = digest(surface["rows"][0]["contract"])
    elif change == "catalog":
        surface["catalogs"]["6"] = "other"
    elif change == "kind":
        surface["denominator_kind"] = "reduced"
    assert not compare_surface_lock(reseal(surface), lock)["ok"]


@pytest.mark.parametrize("bad", ["../escape", "/tmp/escape", "reports/../../escape", "C:\\escape"])
def test_path_escape_rejected(tmp_path, bad):
    fixture = list(setup(tmp_path))
    fixture[4]["groups"][0]["report_root"] = bad
    fixture[4] = reseal(fixture[4])
    with pytest.raises(ValueError, match="path"):
        index(fixture)


def test_symlink_run_component_rejected(tmp_path):
    fixture = setup(tmp_path)
    folder = fixture[1] / "reports/full"
    relocated = fixture[1] / "relocated"
    folder.rename(relocated)
    folder.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        index(fixture)


def test_changed_expected_bytes_rejected_before_join(tmp_path):
    fixture = setup(tmp_path)
    (fixture[0] / "expected.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        index(fixture)


@pytest.mark.parametrize(
    "change", ["empty_groups", "duplicate", "missing_path", "reduced_modes", "bad_hash"]
)
def test_bad_or_reduced_plan_rejected(tmp_path, change):
    fixture = list(setup(tmp_path))
    plan = fixture[4]
    if change == "empty_groups":
        plan["groups"] = []
    elif change == "duplicate":
        plan["groups"] *= 2
    elif change == "missing_path":
        plan["groups"][0]["paths"].pop()
    elif change == "reduced_modes":
        plan["groups"][0]["variants"] = ["full"]
    elif change == "bad_hash":
        plan["groups"][0]["corpus_hash"] = "old"
    fixture[4] = reseal(plan)
    with pytest.raises(ValueError):
        index(fixture)


def test_external_label_alone_never_promotes_entire_stage(tmp_path):
    fixture = setup(tmp_path, legacy=True)
    assert index(fixture)["tradingview_verified"] is False


def test_inputs_are_read_only(tmp_path):
    fixture = setup(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    index(fixture)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "change", ["hash", "empty", "duplicate", "fields", "case", "binding", "contract"]
)
def test_frozen_assignment_set_is_validated_before_reports(tmp_path, change):
    fixture = list(setup(tmp_path))
    lock = read_json(fixture[0] / "assignments-lock.json")
    if change == "hash":
        lock["content_hash"] = "sha256:forged"
    elif change == "empty":
        lock["assignments"] = []
    elif change == "duplicate":
        lock["assignments"] *= 2
    elif change == "fields":
        lock["assignments"][0]["ignored"] = True
    elif change == "case":
        lock["assignments"][0]["case_id"] = "absent"
    elif change == "binding":
        lock["assignments"][0]["call_form"] = "METHOD"
    elif change == "contract":
        lock["assignments"][0]["contract_hash"] = "sha256:other"
    if change != "hash":
        lock = reseal(lock)
    fixture[4]["groups"][0]["assignment_hash"] = lock["content_hash"]
    fixture[4] = reseal(fixture[4])
    write_json(fixture[0] / "assignments-lock.json", lock)
    with pytest.raises(ValueError):
        index(fixture)


def test_report_cannot_drop_one_frozen_case_and_keep_the_other(tmp_path):
    fixture = list(setup(tmp_path))
    host, root, surface, _, plan = fixture
    corpus = read_json(host / "manifest.json")
    extra = deepcopy(corpus["cases"][0])
    extra["id"] = "second"
    corpus["cases"].append(extra)
    corpus = reseal(corpus)
    write_json(host / "manifest.json", corpus)
    frozen = read_json(host / "assignments-lock.json")
    second = {**frozen["assignments"][0], "case_id": "second"}
    frozen["assignments"].append(second)
    frozen["corpus_hash"] = corpus["content_hash"]
    frozen = reseal(frozen)
    write_json(host / "assignments-lock.json", frozen)
    plan["groups"][0].update(
        corpus_hash=corpus["content_hash"], assignment_hash=frozen["content_hash"]
    )
    fixture[4] = reseal(plan)
    for variant in ("full", "compact"):
        for path in EXECUTION_PATHS:
            folder = root / "reports" / variant / path
            observations = read_json(folder / "observations.json")
            observations["second"] = deepcopy(observations["min"])
            assignments = read_json(folder / "assignments.json")
            # Deliberately keep both correct observations but silently assign
            # only one of the two required cases. A rebuilt PASS is insufficient.
            write_json(folder / "observations.json", observations)
            write_json(
                folder / "report.json",
                builtin_evidence_report(
                    surface,
                    host / "manifest.json",
                    observations,
                    corpus_hash=corpus["content_hash"],
                    assignments=assignments,
                ),
            )
    report = index(fixture)
    assert not report["ok"] and report["direct_with_examples_all_paths"] == 0
    assert all("ASSIGNMENT_SET_MISMATCH" in group["reasons"] for group in report["groups"])


def test_cli_rejects_unreviewed_plan_hash_before_writing_output(tmp_path):
    from openpine.verification.__main__ import main

    fixture = setup(tmp_path)
    write_json(fixture[0] / "plan.json", fixture[4])
    output = tmp_path / "report.json"
    with pytest.raises(ValueError, match="plan changed"):
        main(
            [
                "builtin-index",
                "--host-root",
                str(fixture[0]),
                "--evidence",
                str(fixture[1]),
                "--surface-lock",
                str(tmp_path / "not_read.json"),
                "--plan",
                str(fixture[0] / "plan.json"),
                "--expected-plan-hash",
                "wrong",
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


def test_reviewed_remaining_matrix_preserves_original_tasks_and_criteria():
    from pathlib import Path
    from openpine.verification.identity import verify

    root = Path(__file__).resolve().parents[1]
    matrix = read_json(root / "verification/stage2-remaining-matrix.json")
    lock = read_json(root / "verification/stage2-remaining-matrix-lock.json")
    stages = read_json(root / "verification/stages.json")
    original = next(row for row in stages["stages"] if row["id"] == 2)
    verify(matrix, "openpine.stage2_remaining_matrix.v1")
    assert matrix["content_hash"] == lock["content_hash"]
    assert matrix["tasks"] == original["tasks"]
    assert matrix["criteria"] == original["exit_criteria"]
    assert matrix["source_spec_sha256"] == stages["source_spec_sha256"]
    assert [row["id"] for row in matrix["items"]] == lock["ids"]
    assert len(set(lock["ids"])) == len(lock["ids"]) == 16
    assert {row["criterion"] for row in matrix["items"]} == set(matrix["criteria"])
    assert matrix["full_stage2_accepted"] is matrix["tradingview_verified"] is False
    for row in matrix["items"]:
        assert row["owner"] and row["completion"] and row["evidence"]
        assert row["status"] in {
            "open", "partial", "unresolved_authority", "local_gate_ready",
            "implemented_local_pending_joint",
        }
        if row["status"] == "implemented_local_pending_joint":
            # A completed local implementation still cannot grant joint acceptance.
            assert matrix["full_stage2_accepted"] is False
            assert any(path.startswith("rc6_tests/") for path in row["evidence"])
        for path in row["evidence"]:
            assert (root / path).is_file()


def test_committed_evidence_plan_and_assignment_locks_are_complete():
    from pathlib import Path
    from openpine.verification.conformance import load_corpus
    from openpine.verification.evidence_index import _groups
    from openpine.verification.identity import verify

    root = Path(__file__).resolve().parents[1]
    plan = read_json(root / "verification/stage2-evidence-plan.json")
    lock = read_json(root / "verification/stage2-evidence-plan-lock.json")
    assert plan["content_hash"] == lock["plan_hash"]
    groups = _groups(plan)
    assert len(groups) == lock["group_count"] == 12
    assert (
        sum(len(g["variants"]) * len(g["paths"]) for g in groups)
        == lock["expected_group_paths"]
        == 100
    )
    for group in groups:
        corpus = load_corpus(root / group["corpus"])
        assert corpus["content_hash"] == group["corpus_hash"]
        assignment = read_json(root / group["assignment_lock"])
        verify(assignment, "openpine.builtin_assignment_lock.v1")
        assert assignment["content_hash"] == group["assignment_hash"]
        assert assignment["corpus_hash"] == corpus["content_hash"]


def test_unassigned_unverified_mismatch_is_visible_and_blocks_complete_group(tmp_path):
    fixture = list(setup(tmp_path))
    host, root, surface, _, plan = fixture
    corpus = read_json(host / "manifest.json")
    corpus["cases"].append({**deepcopy(corpus["cases"][0]), "id": "uncertain"})
    corpus = reseal(corpus)
    write_json(host / "manifest.json", corpus)
    frozen = read_json(host / "assignments-lock.json")
    frozen["corpus_hash"] = corpus["content_hash"]
    frozen = reseal(frozen)
    write_json(host / "assignments-lock.json", frozen)
    plan["groups"][0].update(
        corpus_hash=corpus["content_hash"], assignment_hash=frozen["content_hash"]
    )
    fixture[4] = reseal(plan)
    for variant in ("full", "compact"):
        for path in EXECUTION_PATHS:
            folder = root / "reports" / variant / path
            observations = read_json(folder / "observations.json")
            observations["uncertain"] = {
                **deepcopy(observations["min"]),
                "events": [{"bar": 0, "value": 999}],
                "semantic_authority": {"classification": "UNVERIFIED"},
            }
            assignments = read_json(folder / "assignments.json")
            write_json(folder / "observations.json", observations)
            write_json(
                folder / "report.json",
                builtin_evidence_report(
                    surface,
                    host / "manifest.json",
                    observations,
                    corpus_hash=corpus["content_hash"],
                    assignments=assignments,
                ),
            )
    report = index(fixture)
    # The valid assigned example remains valid, but cannot certify the entire corpus.
    assert report["direct_with_examples_all_paths"] == 1
    assert not report["ok"] and report["passed_group_paths"] == 0
    for group in report["groups"]:
        assert group["unassigned_cases"] == ["uncertain"]
        assert group["unassigned_outcomes"][0]["authority"] == "UNVERIFIED"
        assert group["unassigned_outcomes"][0]["first_divergence"] is not None
        assert "UNVERIFIED_EXPECTATION_MISMATCH" in group["reasons"]


def test_only_three_pinned_v5_numeric_authority_gaps_can_be_deferred():
    from pathlib import Path
    from openpine.verification.conformance import load_corpus
    from openpine.verification.evidence_index import _temporary_tonumber_gaps

    root = Path(__file__).resolve().parents[1]
    corpus = load_corpus(root / "verification/builtin-string-operations-v1/manifest.json")
    ids = ("manual-tonumber-7-v5", "manual-tonumber-8-v5", "manual-tonumber-9-v5")
    observations = {}
    details = []
    for case_id in ids:
        observations[case_id] = {
            "semantic_authority": {"classification": "UNVERIFIED", "confirmed": False,
                "independent_receipt_sha256": "e21bf08a0f41341edcc47c8c5d70ab4c8ac9ef73150d3969c91d7aaea3010f6b"},
            "source_identity": PINS, "transcript_mode": "full",
        }
        details.append({"id": case_id, "status": "RUNTIME_MISMATCH", "authority": "UNVERIFIED",
                        "first_divergence": "events"})
    assert _temporary_tonumber_gaps(root, corpus, details, observations, PINS, "full") == list(ids)
    for case_id in ids:
        altered = deepcopy(observations)
        altered[case_id]["source_identity"] = {**PINS, "pinelib": "z" * 40}
        assert _temporary_tonumber_gaps(root, corpus, details, altered, PINS, "full") == []
    assert _temporary_tonumber_gaps(root, corpus, details[:-1], observations, PINS, "full") == []
    assert _temporary_tonumber_gaps(root, corpus, [*details, {"id": "other", "status": "RUNTIME_MISMATCH", "authority": "UNVERIFIED"}], observations, PINS, "full") == []
    altered = deepcopy(observations)
    altered[ids[0]]["semantic_authority"]["confirmed"] = True
    assert _temporary_tonumber_gaps(root, corpus, details, altered, PINS, "full") == []


def test_provisional_numeric_group_never_counts_as_semantic_pass(tmp_path, monkeypatch):
    from openpine.verification import evidence_index

    fixture = list(setup(tmp_path))
    host, root, surface, _, plan = fixture
    corpus = read_json(host / "manifest.json")
    corpus["cases"].append({**deepcopy(corpus["cases"][0]), "id": "uncertain"})
    corpus = reseal(corpus)
    write_json(host / "manifest.json", corpus)
    frozen = read_json(host / "assignments-lock.json")
    frozen["corpus_hash"] = corpus["content_hash"]
    frozen = reseal(frozen)
    write_json(host / "assignments-lock.json", frozen)
    plan["groups"][0].update(id="builtin-string-operations", corpus_hash=corpus["content_hash"], assignment_hash=frozen["content_hash"])
    fixture[4] = reseal(plan)
    for variant in ("full", "compact"):
        for path in EXECUTION_PATHS:
            folder = root / "reports" / variant / path
            observations = read_json(folder / "observations.json")
            observations["uncertain"] = {**deepcopy(observations["min"]),
                "events": [{"bar": 0, "value": 999}],
                "semantic_authority": {"classification": "UNVERIFIED"}}
            assignments = read_json(folder / "assignments.json")
            write_json(folder / "observations.json", observations)
            write_json(folder / "report.json", builtin_evidence_report(
                surface, host / "manifest.json", observations,
                corpus_hash=corpus["content_hash"], assignments=assignments,
            ))
    monkeypatch.setattr(evidence_index, "_temporary_tonumber_gaps", lambda *args: ["uncertain"])
    report = index(fixture)
    assert report["denominator"] == 1 and report["required_group_paths"] == 10
    assert report["passed_group_paths"] == 0 and report["deferred_group_paths"] == 10
    assert all(g["status"] == "TEMPORARY_UNVERIFIED" and g["deferred_cases"] == ["uncertain"] for g in report["groups"])
    assert report["provisional_gate_ok"] and not report["ok"]
    assert report["full_builtin_expected_accepted"] is report["full_stage2_accepted"] is False

    # A different unresolved failure cannot be hidden behind the three-case exception.
    corpus["cases"].append({**deepcopy(corpus["cases"][0]), "id": "other"})
    corpus = reseal(corpus)
    write_json(host / "manifest.json", corpus)
    frozen["corpus_hash"] = corpus["content_hash"]
    frozen = reseal(frozen)
    write_json(host / "assignments-lock.json", frozen)
    plan["groups"][0].update(corpus_hash=corpus["content_hash"], assignment_hash=frozen["content_hash"])
    fixture[4] = reseal(plan)
    for variant in ("full", "compact"):
        for path in EXECUTION_PATHS:
            folder = root / "reports" / variant / path
            observations = read_json(folder / "observations.json")
            observations["other"] = {**deepcopy(observations["min"]), "events": [{"bar": 0, "value": 444}],
                                     "semantic_authority": {"classification": "SUPPORTED"}}
            assignments = read_json(folder / "assignments.json")
            write_json(folder / "observations.json", observations)
            write_json(folder / "report.json", builtin_evidence_report(
                surface, host / "manifest.json", observations,
                corpus_hash=corpus["content_hash"], assignments=assignments,
            ))
    report = index(fixture)
    assert not report["provisional_gate_ok"] and report["deferred_group_paths"] == 0
    assert all(group["status"] == "FAILED" for group in report["groups"])


def test_provisional_cli_exit_does_not_forge_full_acceptance(tmp_path, monkeypatch):
    from openpine.verification.__main__ import main
    from openpine.verification import builtins, evidence_index, stage2_remaining

    fixture = setup(tmp_path)
    host, evidence, surface, lock, plan = fixture
    write_json(host / "plan.json", plan)
    write_json(host / "surface-lock.json", lock)
    write_json(host / "docs/RC6_LIFECYCLE_SOURCES.json", PINS)
    report = index(fixture)
    report = reseal({**report, "ok": False, "provisional_gate_ok": True,
                     "deferred_group_paths": 10, "full_stage2_accepted": False})
    monkeypatch.setattr(builtins, "build_builtin_surface", lambda: surface)
    monkeypatch.setattr(evidence_index, "build_evidence_index", lambda *a, **k: report)
    output = tmp_path / "index.json"
    args = ["builtin-index", "--host-root", str(host), "--evidence", str(evidence),
            "--surface-lock", str(host / "surface-lock.json"), "--plan", str(host / "plan.json"),
            "--expected-plan-hash", plan["content_hash"], "--output", str(output)]
    assert main(args) == 0
    assert read_json(output)["ok"] is False
    failed = reseal({**report, "provisional_gate_ok": False})
    monkeypatch.setattr(evidence_index, "build_evidence_index", lambda *a, **k: failed)
    assert main(args) == 1

    monkeypatch.setattr(stage2_remaining, "build_stage2_remaining", lambda *a: report)
    args = ["stage2-remaining", "--host-root", str(host),
            "--builtin-index", str(output), "--output", str(tmp_path / "remaining.json")]
    assert main(args) == 0
    assert read_json(tmp_path / "remaining.json")["full_stage2_accepted"] is False
    monkeypatch.setattr(stage2_remaining, "build_stage2_remaining", lambda *a: failed)
    assert main(args) == 1


def test_stage2_remainder_keeps_provisional_cases_open(tmp_path):
    from pathlib import Path
    from openpine.verification.stage2_remaining import build_stage2_remaining

    root = Path(__file__).resolve().parents[1]
    report = index(setup(tmp_path))
    pins = read_json(root / "docs/RC6_LIFECYCLE_SOURCES.json")
    plan_lock = read_json(root / "verification/stage2-evidence-plan-lock.json")
    surface_lock = read_json(root / "verification/stage2-callable-lock.json")
    groups = [{**row, "status": "TEMPORARY_UNVERIFIED",
               "deferred_cases": ["manual-tonumber-7-v5", "manual-tonumber-8-v5", "manual-tonumber-9-v5"]}
              for row in report["groups"]]
    report = reseal({**report, "groups": groups, "plan_hash": plan_lock["plan_hash"],
                     "lock_hash": surface_lock["content_hash"], "source_pins": pins,
                     "ok": False, "all_declared_runs_passed": False,
                     "provisional_gate_ok": True, "passed_group_paths": 0,
                     "deferred_group_paths": len(groups),
                     "temporary_unverified_cases": groups[0]["deferred_cases"]})
    remaining = build_stage2_remaining(root, report)
    assert remaining["provisional_gate_ok"] is True
    assert remaining["builtin_index_replay_passed"] is remaining["ok"] is False
    assert remaining["temporary_unverified_cases"] == groups[0]["deferred_cases"]
    assert remaining["full_stage2_accepted"] is remaining["tradingview_verified"] is False
