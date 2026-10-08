"""Public job output stays closed even when private inputs contain secrets."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

from openpine.verification import qualification_public as public

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
SECRET = "private-token-/raw/path-PID-12345\n::error::secret\x1b[31m"


@pytest.fixture
def host(tmp_path):
    folder = tmp_path / "host"
    for name in ("verification/protected-qualification-public-allowlist.json",
                 "candidates/stack-candidate-5.0.0-rc.6.template.json"):
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return folder


def policy(host):
    return host / "verification/protected-qualification-public-allowlist.json"


def row(code="family-observation-errors", placement="A"):
    stage, completion = "family-receipt", "not-reached"
    if code not in public.FAMILY_ERRORS:
        stage = "setup"
    if code == "nonzero-exit":
        stage, completion = "neighbour-wait", "observed-false"
    elif code in {"missing-result", "invalid-result"}:
        stage, completion = "native-result", "observed-false"
    fault = {"receipt-unexpected": "controller-sigkill", "command-exit-mismatch": "sigint"}.get(code, "timeout")
    return {"placement": placement, "mode": "interactive", "fault": fault,
            "ok": False, "automatic_cleanup": True, "neighbour_survived": True,
            "neighbour_completed": False, "case_stage": stage, "case_error": code,
            "neighbour_completion_state": completion}


def projection(host, rows):
    return public.project(SHA, public.declared_commits(host, SHA), rows, policy(host),
                          stage="matrix-A", error="command-failed")


def cli(host, folder, monkeypatch, outcome="failure"):
    monkeypatch.setattr(sys, "argv", ["guard", "--host", str(host), "--folder", str(folder),
                                     "--candidate-sha", SHA, "--driver-outcome", outcome])
    return public.main()


@pytest.mark.parametrize("code", sorted(public.CASE_ERRORS))
def test_every_approved_code_has_a_fixed_explanation(host, code):
    lines = public.diagnostic_lines(projection(host, [row(code)]), policy(host))
    assert len(lines) == 2
    assert f"code={code} " in lines[1]
    assert lines[1].endswith(public.CASE_EXPLANATIONS[code])
    assert "placement=A mode=interactive" in lines[1]
    assert "stage=" + row(code)["case_stage"] in lines[1]
    assert SECRET not in "\n".join(lines)


def test_actual_cli_prints_failed_cases_and_summary_without_private_context(host, tmp_path, monkeypatch, capsys):
    rows = [row(), row("nonzero-exit", "B")]
    for item in rows:
        item.update(error=SECRET, error_type=SECRET, traceback=SECRET, stdout=SECRET, path=SECRET, pid=12345)
    value = projection(host, rows)
    folder = tmp_path / "public"
    public.write_projection(folder, value)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    summary = tmp_path / "step-summary"
    summary.write_text("Previous step content\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli(host, folder, monkeypatch) == 0  # Valid failed projection remains uploadable.
    output = capsys.readouterr()
    lines = output.out.splitlines()
    assert json.loads(lines[0]) == {"stage": "matrix-A", "error": "command-failed", "ok": False}
    assert "code=family-observation-errors" in lines[2]
    assert "placement=B" in lines[3] and "code=nonzero-exit" in lines[3]
    assert summary.read_text() == "Previous step content\n\n```text\n" + "\n".join(lines[1:]) + "\n```\n"
    assert SECRET not in output.out + summary.read_text()
    assert "12345" not in output.out and output.err == ""
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}
    assert set(before) == {"projection.json", "projection.sha256"}


@pytest.mark.parametrize("mutation", ["enum", "private-field", "top-field", "contradiction", "derived-outcome"])
def test_malformed_return_value_is_rejected_before_any_case_or_summary_output(host, tmp_path, monkeypatch, capsys, mutation):
    value = copy.deepcopy(projection(host, [row()]))
    if mutation == "enum":
        value["cases"][0]["case_error"] = SECRET
    elif mutation == "private-field":
        value["cases"][0]["stderr"] = SECRET
    elif mutation == "top-field":
        value["stderr"] = SECRET
    elif mutation == "contradiction":
        value["cases"][0]["neighbour_completed"] = True
    else:
        value["ok"] = True
    monkeypatch.setattr(public, "ensure_projection", lambda *args: value)
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli(host, tmp_path / "public", monkeypatch) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"stage": "publication", "error": "projection-invalid", "ok": False}
    assert output.err == "" and SECRET not in output.out and not summary.exists()


def test_malformed_projection_file_cannot_inject_job_commands(host, tmp_path, monkeypatch, capsys):
    value = projection(host, [row()])
    folder = tmp_path / "public"
    public.write_projection(folder, value)
    value["cases"][0]["case_error"] = SECRET
    raw = json.dumps(value).encode()
    (folder / "projection.json").write_bytes(raw)
    (folder / "projection.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cli(host, folder, monkeypatch) == 0
    output = capsys.readouterr()
    assert "projection-invalid" in output.out and "FAILED case:" not in output.out
    assert SECRET not in output.out and "::error::" not in output.out and output.err == ""


@pytest.mark.parametrize("key", sorted(public.TOP_BOOLEANS))
@pytest.mark.parametrize("number", [0, 1])
def test_numeric_top_level_outcomes_are_rejected_at_the_print_boundary(host, tmp_path, monkeypatch, capsys, key, number):
    value = projection(host, [row()])
    value[key] = number
    monkeypatch.setattr(public, "ensure_projection", lambda *args: value)
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli(host, tmp_path / "public", monkeypatch) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"stage": "publication", "error": "projection-invalid", "ok": False}
    assert output.err == "" and not summary.exists()


def test_summary_write_failure_keeps_visible_diagnostic_and_original_guard_status(host, tmp_path, monkeypatch, capsys):
    folder = tmp_path / "public"
    public.write_projection(folder, projection(host, [row()]))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))  # Directory cannot be appended.
    assert cli(host, folder, monkeypatch) == 0
    output = capsys.readouterr()
    assert "code=family-observation-errors" in output.out
    assert output.out.endswith("Public diagnostic summary could not be written.\n")
    assert str(tmp_path) not in output.out and output.err == ""


def test_successful_matrix_never_prints_failed_case(host, tmp_path, monkeypatch, capsys):
    rows = []
    for placement in ("A", "B"):
        for mode in ("interactive", "bulk_backtest"):
            for fault in ("timeout", "sigint", "controller-sigkill"):
                item = row(placement=placement)
                item.update(mode=mode, fault=fault, ok=True, neighbour_completed=True,
                            case_stage="complete", case_error="none", neighbour_completion_state="observed-true")
                rows.append(item)
    value = public.project(SHA, public.declared_commits(host, SHA), rows, policy(host), owner_checks_passed=True)
    folder = tmp_path / "public"
    public.write_projection(folder, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cli(host, folder, monkeypatch, "success") == 0
    output = capsys.readouterr()
    assert "stage=complete code=none ok=true" in output.out
    assert "FAILED case:" not in output.out and output.err == ""


def test_missing_cases_are_reported_without_inventing_an_exception(host, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cli(host, tmp_path / "public", monkeypatch, "skipped") == 0
    output = capsys.readouterr()
    assert "stage=workflow-driver code=missing-output" in output.out
    assert "No failed case diagnostic was recorded" in output.out
    assert "FAILED case:" not in output.out and output.err == ""
