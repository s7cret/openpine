"""Public metadata contracts; synthetic records never prove protected isolation."""

from copy import deepcopy
from itertools import product
from pathlib import Path
import subprocess

import pytest

from openpine.verification import qualification_public as public
from openpine.verification.qualification_hosted import QualificationFailure, error_code

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "verification/protected-qualification-public-allowlist.json"


def successful_rows():
    return [
        dict(
            placement=p,
            mode=m,
            fault=f,
            ok=True,
            automatic_cleanup=True,
            neighbour_survived=True,
            neighbour_completed=True,
            case_stage="complete",
            case_error="none",
            neighbour_completion_state="observed-true",
        )
        for p, m, f in product(
            ["A", "B"],
            ["interactive", "bulk_backtest"],
            ["timeout", "sigint", "controller-sigkill"],
        )
    ]


def projection(rows=None, **kwargs):
    return public.project(
        "a" * 40,
        {c: "a" * 40 for c in public.COMPONENTS},
        successful_rows() if rows is None else rows,
        ALLOWLIST,
        **kwargs,
    )


def test_complete_metadata_is_revalidated_but_does_not_close_durability(tmp_path):
    value = projection(owner_checks_passed=True)
    assert value["ok"] and value["protected_matrix_passed"]
    assert not value["raw_primaries_durable"] and not value["full_qualification_accepted"]
    folder = tmp_path / "public"
    public.write_projection(folder, value)
    assert public.validate_projection(folder, ALLOWLIST) == value
    failed = projection(owner_checks_passed=False, stage="outcome", error="incomplete")
    public.replace_projection(folder, failed)
    assert public.validate_projection(folder, ALLOWLIST) == failed
    assert "ok=false" in public.diagnostic_lines(failed, ALLOWLIST)[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("placement", "C"),
        ("mode", "unsafe"),
        ("fault", "terminate"),
        ("ok", 1),
        ("automatic_cleanup", None),
        ("neighbour_completed", False),
        ("neighbour_survived", False),
        ("case_stage", "fault"),
        ("case_error", "failed"),
        ("neighbour_completion_state", "not-reached"),
    ],
)
def test_success_record_cannot_hide_missing_observations(field, value):
    rows = successful_rows()
    rows[0][field] = value
    with pytest.raises(ValueError):
        projection(rows)


def test_duplicate_case_does_not_replace_missing_matrix_member():
    rows = successful_rows()
    rows[-1] = deepcopy(rows[0])
    with pytest.raises(ValueError, match="duplicate"):
        projection(rows)


@pytest.mark.parametrize("mutation", ["foreign", "hash", "symlink", "outcome", "noncanonical"])
def test_public_files_reject_foreign_or_unbound_outcomes(tmp_path, mutation):
    import hashlib
    import json

    folder = tmp_path / "public"
    public.write_projection(folder, projection())
    target = folder / "projection.json"
    if mutation == "foreign":
        (folder / "raw-secret.txt").write_text("private")
    elif mutation == "hash":
        (folder / "projection.sha256").write_text("0" * 64 + "\n")
    elif mutation == "symlink":
        raw = target.read_bytes()
        target.unlink()
        (tmp_path / "linked").write_bytes(raw)
        target.symlink_to(tmp_path / "linked")
    else:
        value = projection()
        if mutation == "outcome":
            value["ok"] = True
        raw = json.dumps(value, indent=2).encode()
        target.write_bytes(raw)
        (folder / "projection.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(ValueError):
        public.validate_projection(folder, ALLOWLIST)


@pytest.mark.parametrize(
    "error,stage,expected",
    [
        (KeyboardInterrupt(), "identity", "interrupted"),
        (SystemExit(1), "prepare", "interrupted"),
        (TimeoutError(), "identity", "timeout"),
        (subprocess.TimeoutExpired("cmd", 1), "prepare", "timeout"),
        (FileNotFoundError(), "prepare", "missing-input"),
        (OSError(), "prepare", "io-failed"),
        (ValueError(), "identity", "identity-mismatch"),
        (KeyError(), "matrix-A", "invalid-input"),
        (RuntimeError(), "restore-A", "command-failed"),
        (AssertionError(), "identity", "unexpected-error"),
        (QualificationFailure("disk-reserve"), "prepare", "disk-reserve"),
    ],
)
def test_hosted_failures_keep_closed_diagnostic_categories(error, stage, expected):
    assert error_code(error, stage) == expected


@pytest.mark.parametrize("code", ["none", "incomplete", "private-error"])
def test_failure_category_cannot_claim_success_or_emit_arbitrary_text(code):
    with pytest.raises(ValueError):
        QualificationFailure(code)
