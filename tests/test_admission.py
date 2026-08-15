from __future__ import annotations

import os

import pytest
from openpine_contracts import AdmitError, RunMode

from openpine.admission import admit_run, parse_run_mode


def test_unknown_mode_is_hard_fail() -> None:
    with pytest.raises(AdmitError, match="unknown run mode") as exc:
        parse_run_mode("prod")
    assert exc.value.code == "UNKNOWN_RUN_MODE"


def test_env_drift_flag_cannot_bypass_stack_lock() -> None:
    os.environ["OPENPINE_ALLOW_STACK_LOCK_DRIFT"] = "1"
    try:
        with pytest.raises(AdmitError, match="stack_id drift"):
            admit_run(
                mode=RunMode.BACKTEST,
                stack_id="drifted",
                expected_stack_id="canonical",
            )
    finally:
        os.environ.pop("OPENPINE_ALLOW_STACK_LOCK_DRIFT", None)


def test_live_cannot_override_even_in_local_dev() -> None:
    with pytest.raises(AdmitError, match="stack_id drift"):
        admit_run(
            mode="live",
            stack_id="drifted",
            expected_stack_id="canonical",
            profile="local-dev",
            explicit_override=True,
        )


def test_local_dev_backtest_override_requires_explicit_flag() -> None:
    with pytest.raises(AdmitError, match="stack_id drift"):
        admit_run(
            mode=RunMode.BACKTEST,
            stack_id="drifted",
            expected_stack_id="canonical",
            profile="local-dev",
            explicit_override=False,
        )
    result = admit_run(
        mode=RunMode.BACKTEST,
        stack_id="drifted",
        expected_stack_id="canonical",
        profile="local-dev",
        explicit_override=True,
    )
    assert result.admitted is True


def test_matching_stack_is_admitted() -> None:
    result = admit_run(mode="BACKTEST", stack_id="openpine-5.0")
    assert result.admitted is True
    assert result.code == "ADMIT_OK"


def test_admission_module_does_not_read_drift_env() -> None:
    from pathlib import Path

    text = Path("openpine/admission.py").read_text(encoding="utf-8")
    assert "OPENPINE_ALLOW" not in text


def test_semantic_profile_admission_fail_closed() -> None:
    from openpine.admission import admit_semantic_profile
    from openpine_contracts import SemanticProfile

    assert (
        admit_semantic_profile(profile="strict_5x", source="generated_artifact.v2")
        is SemanticProfile.STRICT_5X
    )
    with pytest.raises(AdmitError, match="semantic profile required") as missing:
        admit_semantic_profile(profile=None, source="live")
    assert missing.value.code == "SEMANTIC_PROFILE_REQUIRED"
    with pytest.raises(AdmitError, match="unknown semantic profile") as unknown:
        admit_semantic_profile(profile="nope", source="backtest")
    assert unknown.value.code == "UNKNOWN_SEMANTIC_PROFILE"
    with pytest.raises(AdmitError, match="legacy") as live:
        admit_semantic_profile(profile="legacy_4x", source="live")
    assert live.value.code == "LEGACY_PROFILE_NOT_ALLOWED"
    assert (
        admit_semantic_profile(profile="legacy_4x", source="live", allow_legacy=True)
        is SemanticProfile.LEGACY_4X
    )
