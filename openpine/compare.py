"""Compare two runs by semantic profile. Different profiles are a warning, not a mix."""

from __future__ import annotations

from typing import Any, Mapping

PROFILE_REF_PREFIX = "semantic_profile:"


def compare_semantic_profiles(
    left: object | None, right: object | None
) -> dict[str, Any]:
    if (
        left is None
        or right is None
        or str(left).strip() == ""
        or str(right).strip() == ""
    ):
        return {
            "ok": False,
            "warning": True,
            "code": "SEMANTIC_PROFILE_MISSING",
            "message": "cannot compare runs without an explicit semantic profile",
            "left": None if left is None or str(left).strip() == "" else str(left),
            "right": None if right is None or str(right).strip() == "" else str(right),
        }
    left_s = str(left)
    right_s = str(right)
    if left_s != right_s:
        return {
            "ok": False,
            "warning": True,
            "code": "SEMANTIC_PROFILE_MISMATCH",
            "message": "same strategy, different semantic profile — not a silent mix",
            "left": left_s,
            "right": right_s,
        }
    return {
        "ok": True,
        "warning": False,
        "code": "SEMANTIC_PROFILE_MATCH",
        "message": "semantic profiles match",
        "left": left_s,
        "right": right_s,
    }


def profile_from_job(job: Mapping[str, Any]) -> str | None:
    for ref in job.get("input_artifact_refs") or []:
        text = str(ref)
        if text.startswith(PROFILE_REF_PREFIX):
            value = text[len(PROFILE_REF_PREFIX) :].strip()
            if value:
                return value
    return None
