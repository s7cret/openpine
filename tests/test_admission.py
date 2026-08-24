from __future__ import annotations

import os
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from openpine_contracts import AdmitError, RunMode

from openpine.admission import (
    RunAdmissionIdentity,
    admit_run,
    candidate_manifest_hash,
    deployment_identity_from_candidate,
    load_active_deployment_identity,
    parse_run_mode,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64


def _candidate() -> dict:
    payload = {
        "schema": "openpine.stack-candidate.v2",
        "stage": "wheel-bound",
        "id": "5.0.0-rc.4",
        "components": {
            "openpine": {
                "version": "5.0.0rc4",
                "wheel": {"filename": "openpine.whl", "sha256": HASH_B},
            },
            "openpine-contracts": {
                "version": "5.0.0rc4",
                "wheel": {"filename": "contracts.whl", "sha256": HASH_C},
            },
        },
        "schema_hashes": {
            "openpine.run.v2": HASH_C,
            "openpine.worker.protocol.v2": HASH_D,
        },
        "admission": {
            "capabilities": ["closed_bar", "deterministic_clock"],
            "semantic_profiles": ["strict_5x"],
            "finality_policies": ["CLOSED_BAR_ONLY"],
            "warmup_policies": ["CALC_ONLY"],
            "score_policies": ["ALL_BARS"],
        },
    }
    payload["manifest_hash"] = candidate_manifest_hash(payload)
    return payload


def _run_identity(**updates: object) -> RunAdmissionIdentity:
    values: dict[str, object] = {
        "stack_manifest_hash": _candidate()["manifest_hash"],
        "wheel_identities": (
            ("openpine", "5.0.0rc4", HASH_B),
            ("openpine-contracts", "5.0.0rc4", HASH_C),
        ),
        "schema_hashes": {
            "openpine.run.v2": HASH_C,
            "openpine.worker.protocol.v2": HASH_D,
        },
        "generated_artifact_hash": HASH_B,
        "data_snapshot_hash": HASH_C,
        "semantic_profile": "strict_5x",
        "finality_policy": "CLOSED_BAR_ONLY",
        "warmup_policy": "CALC_ONLY",
        "score_policy": "ALL_BARS",
        "required_capabilities": ("closed_bar",),
    }
    values.update(updates)
    return RunAdmissionIdentity(**values)


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
                deployment=deployment_identity_from_candidate(_candidate()),
                run=_run_identity(stack_manifest_hash=HASH_D),
                expected_artifact_hash=HASH_B,
                expected_data_snapshot_hash=HASH_C,
            )
    finally:
        os.environ.pop("OPENPINE_ALLOW_STACK_LOCK_DRIFT", None)


def test_live_cannot_override_even_in_local_dev() -> None:
    with pytest.raises(AdmitError, match="stack_id drift"):
        admit_run(
            mode="live",
            deployment=deployment_identity_from_candidate(_candidate()),
            run=_run_identity(stack_manifest_hash=HASH_D),
            expected_artifact_hash=HASH_B,
            expected_data_snapshot_hash=HASH_C,
            profile="local-dev",
            explicit_override=True,
        )


def test_local_dev_backtest_override_requires_explicit_flag() -> None:
    with pytest.raises(AdmitError, match="stack_id drift"):
        admit_run(
            mode=RunMode.BACKTEST,
            deployment=deployment_identity_from_candidate(_candidate()),
            run=_run_identity(stack_manifest_hash=HASH_D),
            expected_artifact_hash=HASH_B,
            expected_data_snapshot_hash=HASH_C,
            profile="local-dev",
            explicit_override=False,
        )
    result = admit_run(
        mode=RunMode.BACKTEST,
        deployment=deployment_identity_from_candidate(_candidate()),
        run=_run_identity(stack_manifest_hash=HASH_D),
        expected_artifact_hash=HASH_B,
        expected_data_snapshot_hash=HASH_C,
        profile="local-dev",
        explicit_override=True,
    )
    assert result.admitted is True


def test_matching_stack_is_admitted() -> None:
    result = admit_run(
        mode="BACKTEST",
        deployment=deployment_identity_from_candidate(_candidate()),
        run=_run_identity(),
        expected_artifact_hash=HASH_B,
        expected_data_snapshot_hash=HASH_C,
    )
    assert result.admitted is True
    assert result.code == "ADMIT_OK"


def test_candidate_deployment_identity_requires_wheel_schema_and_policy_evidence() -> None:
    deployment = deployment_identity_from_candidate(_candidate())
    assert deployment.stack_id == "5.0.0-rc.4"
    assert deployment.stack_manifest_hash == _candidate()["manifest_hash"]
    assert deployment.wheel_identities[0] == ("openpine", "5.0.0rc4", HASH_B)
    assert deployment.schema_hashes["openpine.run.v2"] == HASH_C

    source = _candidate()
    source["stage"] = "source"
    with pytest.raises(AdmitError, match="wheel-bound"):
        deployment_identity_from_candidate(source)

    missing = _candidate()
    missing.pop("schema_hashes")
    missing["manifest_hash"] = candidate_manifest_hash(missing)
    with pytest.raises(AdmitError, match="schema hashes"):
        deployment_identity_from_candidate(missing)


def test_active_deployment_verifies_wheel_bytes_installed_versions_and_schemas(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel_bytes = {
        "openpine.whl": b"openpine-wheel",
        "contracts.whl": b"contracts-wheel",
    }
    for name, content in wheel_bytes.items():
        (wheelhouse / name).write_bytes(content)

    candidate = _candidate()
    candidate["components"]["openpine"]["wheel"]["sha256"] = (
        "sha256:" + sha256(wheel_bytes["openpine.whl"]).hexdigest()
    )
    candidate["components"]["openpine-contracts"]["wheel"]["sha256"] = (
        "sha256:" + sha256(wheel_bytes["contracts.whl"]).hexdigest()
    )
    candidate["manifest_hash"] = candidate_manifest_hash(candidate)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    versions = {"openpine": "5.0.0rc4", "openpine-contracts": "5.0.0rc4"}

    deployment = load_active_deployment_identity(
        path,
        wheelhouse,
        version_reader=versions.__getitem__,
        schema_hashes_reader=lambda: dict(candidate["schema_hashes"]),
    )
    assert deployment.stack_manifest_hash == candidate["manifest_hash"]

    (wheelhouse / "openpine.whl").write_bytes(b"tampered")
    with pytest.raises(AdmitError, match="wheel hash mismatch"):
        load_active_deployment_identity(
            path,
            wheelhouse,
            version_reader=versions.__getitem__,
            schema_hashes_reader=lambda: dict(candidate["schema_hashes"]),
        )

    (wheelhouse / "openpine.whl").write_bytes(wheel_bytes["openpine.whl"])
    with pytest.raises(AdmitError, match="installed version mismatch"):
        load_active_deployment_identity(
            path,
            wheelhouse,
            version_reader=lambda _name: "0.0.0",
            schema_hashes_reader=lambda: dict(candidate["schema_hashes"]),
        )
    with pytest.raises(AdmitError, match="installed schema hashes mismatch"):
        load_active_deployment_identity(
            path,
            wheelhouse,
            version_reader=versions.__getitem__,
            schema_hashes_reader=lambda: {"openpine.run.v2": HASH_A},
        )


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"wheel_identities": (("openpine", "5.0.0rc4", HASH_D),)}, "wheel identities"),
        ({"schema_hashes": {"openpine.run.v2": HASH_D}}, "schema hashes"),
        ({"generated_artifact_hash": HASH_D}, "artifact hash mismatch"),
        ({"data_snapshot_hash": HASH_D}, "data snapshot hash mismatch"),
        ({"required_capabilities": ("missing",)}, "missing capabilities"),
        ({"semantic_profile": "legacy_4x"}, "semantic profile"),
        ({"finality_policy": "ALLOW_OPEN"}, "finality policy"),
    ],
)
def test_run_admission_rejects_every_identity_or_policy_drift(
    updates: dict[str, object], expected: str
) -> None:
    with pytest.raises(AdmitError, match=expected):
        admit_run(
            mode="BACKTEST",
            deployment=deployment_identity_from_candidate(_candidate()),
            run=_run_identity(**updates),
            expected_artifact_hash=HASH_B,
            expected_data_snapshot_hash=HASH_C,
        )


def test_http_admission_uses_active_deployment_identity_not_a_constant() -> None:
    from openpine.gateway.side_effects import require_http_admit

    with pytest.raises(HTTPException) as missing:
        require_http_admit(SimpleNamespace(), "backtest")
    assert missing.value.status_code == 503
    assert missing.value.detail == "ADMISSION_IDENTITY_REQUIRED"

    state = SimpleNamespace(admission_identity=deployment_identity_from_candidate(_candidate()))
    require_http_admit(state, "backtest")


def test_admission_module_does_not_read_drift_env() -> None:
    from pathlib import Path

    text = Path("openpine/admission.py").read_text(encoding="utf-8")
    assert "OPENPINE_ALLOW" not in text
    assert '"sha256:0"' not in text


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
