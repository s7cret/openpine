"""Failure injection and real metadata finalization, never hosted runtime proof."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

import pytest

from openpine.verification import qualification_hosted as hosted
from openpine.verification import qualification_public as public
from scripts.finalize_stack_candidate import CandidateFinalizationError, finalize_candidate
from scripts.materialize_stack_candidate import materialize_candidate

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
SECRET = "fixture-secret-/private/raw-traceback"


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "host"
    for name in ("verification/protected-qualification-public-allowlist.json",
                 "verification/execution-policy.json", "verification/protected-qualification-inventory.json",
                 "candidates/stack-candidate-5.0.0-rc.6.template.json", "pyproject.toml"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    for namespace in ("rc6_tests", "int05_tests"):
        (root / namespace).mkdir()
    return root


def policy(host):
    return host / "verification/protected-qualification-public-allowlist.json"


def source(host):
    return materialize_candidate(json.loads((host / "candidates/stack-candidate-5.0.0-rc.6.template.json").read_bytes()),
        openpine_sha=SHA, created_at_utc="2026-10-07T00:00:00Z", provenance={"builder": "fixture", "run_id": "fixture"})


def wheel(folder, name, version, *, filename=None):
    folder.mkdir(parents=True, exist_ok=True)
    normalized = name.replace("-", "_")
    path = folder / (filename or f"{normalized}-{version}-py3-none-any.whl")
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{normalized}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        if name == "openpine-contracts":
            for schema in ("openpine.run.v2", "openpine.worker.protocol.v2"):
                archive.writestr("openpine_contracts/schemas/" + schema + ".json", json.dumps({"$id": schema}))
    return path


def mixed_wheels(host, folder):
    value = source(host)
    for name, row in value["components"].items():
        wheel(folder, name, row["version"])
    wheel(folder, "pytest", "8.4.1")
    return value


def good_rows():
    return [{"placement": p, "mode": mode, "fault": fault, "ok": True,
             "automatic_cleanup": True, "neighbour_survived": True, "neighbour_completed": True}
            for p in ("A", "B") for mode in hosted.MODES for fault in hosted.FAULTS]


def assert_closed(host, work, stage, error):
    value = public.validate_projection(work / "public", policy(host))
    assert (value["stage"], value["error"], value["ok"]) == (stage, error, False)
    assert value["raw_primaries_durable"] is False and value["full_qualification_accepted"] is False
    assert SECRET not in (work / "public/projection.json").read_text()
    return value


def test_mixed_bundle_rejected_but_exact_candidate_bytes_finalize(host, tmp_path):
    mixed = tmp_path / "mixed"
    value = mixed_wheels(host, mixed)
    with pytest.raises(CandidateFinalizationError, match="wheel set mismatch.*pytest"):
        finalize_candidate(value, mixed)
    selected = hosted.candidate_wheelhouse(value, mixed, tmp_path / "exact")
    assert len(list(selected.iterdir())) == 8 and len(list(mixed.iterdir())) == 9
    final = finalize_candidate(value, selected)
    assert final["stage"] == "wheel-bound"
    for row in final["components"].values():
        original = mixed / row["wheel"]["filename"]
        target = selected / original.name
        assert original.stat().st_ino == target.stat().st_ino
        assert row["wheel"]["sha256"] == "sha256:" + hashlib.sha256(original.read_bytes()).hexdigest()


@pytest.mark.parametrize("mutation,code", [("missing", "missing-input"), ("duplicate", "invalid-input"),
                                          ("symlink", "invalid-input"), ("inventory", "invalid-input")])
def test_candidate_selection_rejects_ambiguous_or_missing_bytes(host, tmp_path, mutation, code):
    mixed = tmp_path / "mixed"
    value = mixed_wheels(host, mixed)
    chosen = next(mixed.glob("openpine-*.whl"))
    if mutation == "missing":
        chosen.unlink()
    elif mutation == "duplicate":
        shutil.copyfile(chosen, mixed / "duplicate.whl")
    elif mutation == "symlink":
        original = tmp_path / "original.whl"
        chosen.rename(original)
        chosen.symlink_to(original)
    else:
        value["components"].pop("optimizer")
    with pytest.raises(hosted.QualificationFailure) as caught:
        hosted.candidate_wheelhouse(value, mixed, tmp_path / "exact")
    assert caught.value.code == code


@pytest.mark.parametrize("stage", sorted(public.STAGES - {"publication", "workflow-driver"}))
def test_every_driver_stage_has_a_checkpoint_before_failure(host, tmp_path, monkeypatch, capsys, stage):
    work = tmp_path / "work"

    def fail(host, work, approved_sha, state):
        state.at(stage)
        assert_closed(host, work, stage, "incomplete")
        raise RuntimeError(SECRET)

    monkeypatch.setattr(hosted, "_prepare_and_run", fail)
    assert hosted.prepare_and_run(host, work, SHA, True) == 1
    assert_closed(host, work, stage, "command-failed" if stage in {"prepare", "restore-A", "restore-B"} else "unexpected-error")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("code", sorted(public.ERRORS - {"none", "incomplete"}))
def test_each_explicit_failure_category_stays_closed(host, tmp_path, monkeypatch, code):
    def fail(host, work, approved_sha, state):
        state.at("candidate-manifest")
        raise hosted.QualificationFailure(code)

    monkeypatch.setattr(hosted, "_prepare_and_run", fail)
    assert hosted.prepare_and_run(host, tmp_path / "work", SHA, True) == 1
    assert_closed(host, tmp_path / "work", "candidate-manifest", code)


@pytest.mark.parametrize("error,code", [(FileNotFoundError(SECRET), "missing-input"),
    (TimeoutError(SECRET), "timeout"), (subprocess.TimeoutExpired(SECRET, 1), "timeout"),
    (OSError(SECRET), "io-failed"), (ValueError(SECRET), "invalid-input"),
    (TypeError(SECRET), "invalid-input"), (KeyError(SECRET), "invalid-input"),
    (KeyboardInterrupt(SECRET), "interrupted"), (SystemExit(SECRET), "interrupted")])
def test_exception_text_never_reaches_public_projection(host, tmp_path, monkeypatch, error, code):
    def fail(host, work, approved_sha, state):
        state.at("int05-B")
        raise error

    monkeypatch.setattr(hosted, "_prepare_and_run", fail)
    assert hosted.prepare_and_run(host, tmp_path / "work", SHA, True) == 1
    assert_closed(host, tmp_path / "work", "int05-B", code)
    private = tmp_path / "work/private"
    assert private.stat().st_mode & 0o777 == 0o700
    assert SECRET in (private / "driver-failure.json").read_text()


def test_existing_attempt_is_never_overwritten(host, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    public.write_projection(work / "public", public.project(SHA, {}, [], policy(host), stage="prepare", error="timeout"))
    original = (work / "public/projection.json").read_bytes()
    assert hosted.prepare_and_run(host, work, SHA, True) == 1
    assert (work / "public/projection.json").read_bytes() == original


def test_unknown_identity_is_reported_without_guessing_pins(host, tmp_path):
    work = tmp_path / "work"
    assert hosted.prepare_and_run(host, work, SECRET, True) == 1
    value = assert_closed(host, work, "identity", "identity-mismatch")
    assert value["candidate_sha"] is None and value["source_commits"] == {}


def test_failed_phase_survives_later_checkpoints(host, tmp_path):
    state = hosted.DiagnosticState(host, tmp_path / "work", SHA, True)
    state.at("int04-A")
    state.fail("command-failed")
    state.at("protected-B")
    state.rows = good_rows()
    state.owners_passed = True
    state.stage = "complete"
    state.publish(complete=True)
    value = assert_closed(host, state.work, "int04-A", "command-failed")
    assert value["protected_matrix_passed"] is True


@pytest.mark.parametrize("owners,partial,expected", [(True, False, True), (False, False, False), (True, True, False)])
def test_only_complete_matrix_and_owner_receipts_can_be_green(host, tmp_path, owners, partial, expected):
    state = hosted.DiagnosticState(host, tmp_path / "work", SHA, True,
        commits=public.declared_commits(host, SHA), rows=good_rows()[:-1] if partial else good_rows(), owners_passed=owners)
    state.stage = "complete"
    value = state.publish(complete=True)
    assert value["ok"] is expected and value["full_qualification_accepted"] is False


def test_unapproved_projection_is_not_written(host, tmp_path):
    state = hosted.DiagnosticState(host, tmp_path / "work", SHA, False)
    state.at("prepare")
    state.fail("command-failed")
    assert state.publish()["ok"] is False and not state.work.exists()


@pytest.mark.parametrize("mutation", ["absent", "one-file", "digest", "extra-field", "case-type", "commits-type"])
def test_independent_guard_recovers_missing_and_malformed_output(host, tmp_path, mutation):
    folder = tmp_path / "work/public"
    if mutation != "absent":
        value = public.project(SHA, public.declared_commits(host, SHA), [], policy(host), stage="prepare", error="timeout")
        public.write_projection(folder, value)
        if mutation == "one-file":
            (folder / "projection.sha256").unlink()
        elif mutation == "digest":
            (folder / "projection.sha256").write_text("0" * 64 + "\n")
        else:
            if mutation == "extra-field":
                value["private"] = SECRET
            elif mutation == "case-type":
                value["cases"] = [SECRET]
            elif mutation == "commits-type":
                value["source_commits"] = [SECRET]
            raw = json.dumps(value).encode()
            (folder / "projection.json").write_bytes(raw)
            (folder / "projection.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    value = public.ensure_projection(host, folder, SHA, "failure")
    assert value["stage"] == "workflow-driver" and value["ok"] is False
    assert value["error"] == ("missing-output" if mutation in {"absent", "one-file"} else "projection-invalid")
    assert public.validate_projection(folder, policy(host)) == value
    assert SECRET not in (folder / "projection.json").read_text()
    assert sum(p.stat().st_size for p in folder.iterdir()) < 1024 * 1024


def test_failed_workflow_cannot_publish_a_green_driver_checkpoint(host, tmp_path):
    folder = tmp_path / "public"
    public.write_projection(folder, public.project(SHA, public.declared_commits(host, SHA), good_rows(), policy(host), owner_checks_passed=True))
    value = public.ensure_projection(host, folder, SHA, "failure")
    assert (value["stage"], value["error"], value["ok"]) == ("workflow-driver", "incomplete", False)


def test_guard_preserves_valid_failure_and_exact_pass(host, tmp_path):
    for outcome in ("failure", "success"):
        folder = tmp_path / outcome
        value = public.project(SHA, public.declared_commits(host, SHA), good_rows(), policy(host),
            owner_checks_passed=True, stage="complete" if outcome == "success" else "matrix-A",
            error="none" if outcome == "success" else "command-failed")
        public.write_projection(folder, value)
        original = (folder / "projection.json").read_bytes()
        assert public.ensure_projection(host, folder, SHA, outcome) == value
        assert (folder / "projection.json").read_bytes() == original


@pytest.mark.parametrize("mutation", ["foreign-file", "file-symlink", "folder-symlink"])
def test_guard_refuses_unsafe_upload_content(host, tmp_path, mutation):
    folder = tmp_path / "public"
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text(SECRET)
    if mutation == "folder-symlink":
        folder.symlink_to(outside, target_is_directory=True)
    else:
        folder.mkdir()
        if mutation == "file-symlink":
            (folder / "projection.json").symlink_to(secret)
        else:
            (folder / "foreign.txt").write_text(SECRET)
    with pytest.raises(ValueError):
        public.ensure_projection(host, folder, SHA, "failure")
    assert secret.read_text() == SECRET


def test_public_cli_failure_exposes_only_closed_constants(host, tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(public, "ensure_projection", fail)
    monkeypatch.setattr("sys.argv", ["guard", "--host", str(host), "--folder", str(tmp_path / "public"),
                                   "--candidate-sha", SHA, "--driver-outcome", "failure"])
    assert public.main() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"stage": "publication", "error": "projection-invalid", "ok": False}
    assert captured.err == "" and SECRET not in captured.out


def test_independent_guard_recovers_a_failed_driver_writer(host, tmp_path, monkeypatch):
    work = tmp_path / "work"

    def fail(*args):
        raise OSError(SECRET)

    monkeypatch.setattr(hosted, "replace_projection", fail)
    assert hosted.prepare_and_run(host, work, SHA, True) == 1
    assert not (work / "public").exists()
    value = public.ensure_projection(host, work / "public", SHA, "failure")
    assert value["error"] == "missing-output" and value["ok"] is False


def test_guard_does_not_invent_missing_source_commits(host, tmp_path):
    (host / "candidates/stack-candidate-5.0.0-rc.6.template.json").unlink()
    value = public.ensure_projection(host, tmp_path / "public", SHA, "skipped")
    assert value["candidate_sha"] == SHA and value["source_commits"] == {} and value["ok"] is False


def test_private_failure_persistence_cannot_block_public_diagnostic(host, tmp_path, monkeypatch):
    def fail(host, work, approved_sha, state):
        state.at("prepare")
        raise RuntimeError(SECRET)

    def failed_write(*args):
        raise OSError(SECRET)

    monkeypatch.setattr(hosted, "_prepare_and_run", fail)
    monkeypatch.setattr(hosted, "write_primary", failed_write)
    assert hosted.prepare_and_run(host, tmp_path / "work", SHA, True) == 1
    assert_closed(host, tmp_path / "work", "prepare", "command-failed")


def test_guard_rejects_hidden_duplicate_key_bytes(host, tmp_path):
    folder = tmp_path / "public"
    value = public.project(SHA, public.declared_commits(host, SHA), [], policy(host), stage="prepare", error="timeout")
    public.write_projection(folder, value)
    raw = (folder / "projection.json").read_bytes()
    raw = b'{"candidate_sha":"' + SECRET.encode() + b'",' + raw[1:]
    (folder / "projection.json").write_bytes(raw)
    (folder / "projection.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    assert json.loads(raw) == value
    fixed = public.ensure_projection(host, folder, SHA, "failure")
    assert fixed["error"] == "projection-invalid" and SECRET not in (folder / "projection.json").read_text()


@pytest.mark.parametrize("missing,stage", [("restored", "restore-A"), ("matrix", "matrix-A"), ("owner", "int04-A")])
def test_actual_pipeline_missing_outputs_are_failures(host, tmp_path, monkeypatch, missing, stage):
    from openpine.verification import execution_ci

    def prepare(host, prepared, version):
        mixed_wheels(host, prepared / "bundle/wheelhouse")

    def restore(bundle, root):
        root.mkdir()
        if missing != "restored":
            hosted.write_primary(root / "restored.json", {"executable": str(root / "venv/bin/python"),
                "roots": {"openpine": str(host)}})

    def call(argv, cwd, output, **kwargs):
        if "execute" in argv and missing != "matrix":
            matrix = Path(argv[argv.index("--work") + 1]) / "matrix.json"
            hosted.write_primary(matrix, {"cases": good_rows()[:6]})

    monkeypatch.setattr(execution_ci, "prepare", prepare)
    monkeypatch.setattr(execution_ci, "restore", restore)
    monkeypatch.setattr(hosted.subprocess, "check_output", lambda *args, **kwargs: SHA)
    monkeypatch.setattr(hosted, "call", call)
    assert hosted.prepare_and_run(host, tmp_path / "work", SHA, True) == 1
    assert_closed(host, tmp_path / "work", stage, "missing-output")


@pytest.mark.parametrize("mutate", [lambda value: value.update(stage=[SECRET]),
    lambda value: value.update(source_commits=[SECRET]), lambda value: value.update(cases=[SECRET]),
    lambda value: value["cases"][0].update(placement=[SECRET])])
def test_projection_types_fail_closed(host, mutate):
    value = {"candidate_sha": SHA, "source_commits": public.declared_commits(host, SHA),
             "cases": copy.deepcopy(good_rows()), "stage": "complete"}
    mutate(value)
    with pytest.raises(ValueError):
        public.project(value["candidate_sha"], value["source_commits"], value["cases"], policy(host), stage=value["stage"])
