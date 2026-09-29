"""Candidate admission rejects malformed identities before any broker can run."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from openpine_contracts import AdmitError

from openpine import admission


_DIGEST = "sha256:" + "a" * 64


def _candidate() -> dict:
    candidate = {
        "schema": "openpine.stack-candidate.v2",
        "stage": "wheel-bound",
        "id": "candidate-a",
        "components": {
            "openpine": {"version": "5.0.0rc6", "wheel": {"sha256": _DIGEST}},
        },
        "schema_hashes": {"openpine.job.v1": _DIGEST},
        "admission": {
            "capabilities": ["compile"],
            "semantic_profiles": ["strict_5x"],
            "finality_policies": ["closed_only"],
            "warmup_policies": ["all"],
            "score_policies": ["default"],
        },
    }
    candidate["manifest_hash"] = admission.candidate_manifest_hash(candidate)
    return candidate


def _resign(candidate: dict) -> dict:
    candidate["manifest_hash"] = admission.candidate_manifest_hash(candidate)
    return candidate


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda c: c.update(schema="wrong"), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(stage="source-bound"), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(id=""), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(components={}), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(components={3: {"version": "5"}}), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c["components"]["openpine"].update(version=None), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c["components"]["openpine"]["wheel"].update(sha256="sha256:" + "0" * 64), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(schema_hashes={}), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(schema_hashes={"": _DIGEST}), "ADMISSION_IDENTITY_INVALID"),
        (lambda c: c.update(admission=None), "ADMISSION_POLICY_INVALID"),
        (lambda c: c["admission"].update(capabilities=["compile", ""]), "ADMISSION_POLICY_INVALID"),
    ],
)
def test_candidate_rejects_invalid_field_even_with_valid_recomputed_seal(change, code):
    candidate = _candidate()
    change(candidate)
    with pytest.raises(AdmitError) as error:
        admission.deployment_identity_from_candidate(_resign(candidate))
    assert error.value.code == code


def test_manifest_digest_binds_candidate_content_and_ignores_its_own_signature():
    candidate = _candidate()
    unsigned = {key: value for key, value in candidate.items() if key != "manifest_hash"}
    canonical = json.dumps({"domain": "openpine.stack-candidate.v2", "payload": unsigned}, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert candidate["manifest_hash"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert admission.candidate_manifest_hash(candidate) == candidate["manifest_hash"]
    candidate["id"] = "other-candidate"
    with pytest.raises(AdmitError) as error:
        admission.deployment_identity_from_candidate(candidate)
    assert error.value.code == "STACK_MANIFEST_HASH_MISMATCH"
    identity = admission.deployment_identity_from_candidate(_resign(candidate))
    assert identity.stack_id == "other-candidate"
    assert identity.wheel_identities == (("openpine", "5.0.0rc6", _DIGEST),)
    assert identity.schema_hashes == {"openpine.job.v1": _DIGEST}


def test_installed_contract_schema_digest_is_bound_to_real_schema_bytes():
    from openpine_contracts import __file__ as contracts_path
    assert contracts_path is not None
    schemas = Path(contracts_path).resolve().parent / "schemas"
    actual = admission.installed_contract_schema_hashes()
    independently_derived = {}
    for path in schemas.glob("*.json"):
        content = path.read_bytes()
        schema = json.loads(content)
        independently_derived[schema["$id"]] = "sha256:" + hashlib.sha256(content).hexdigest()
    assert actual == independently_derived
    assert actual


def test_run_admission_rejects_different_artifact_and_required_capability():
    identity = admission.deployment_identity_from_candidate(_candidate())
    with pytest.raises(AdmitError) as error:
        admission.admit_deployment(mode="paper", deployment=identity, required_capabilities=("unavailable",))
    assert error.value.code == "MISSING_CAPABILITIES"
    result = admission.admit_deployment(mode="paper", deployment=identity)
    assert result.admitted is True
    assert result.details["stack_id"] == "candidate-a"
    with pytest.raises(AdmitError) as error:
        admission.admit_deployment(mode="paper", deployment=replace(identity, wheel_identities=()))
    assert error.value.code == "ADMISSION_IDENTITY_INVALID"
    with pytest.raises(AdmitError) as error:
        admission.admit_deployment(mode="paper", deployment=replace(identity, schema_hashes={}))
    assert error.value.code == "ADMISSION_IDENTITY_INVALID"


def test_invalid_source_manifest_and_missing_wheel_fail_closed(tmp_path: Path):
    manifest = tmp_path / "candidate.json"
    with pytest.raises(AdmitError) as error:
        admission.load_active_deployment_identity(manifest, tmp_path)
    assert error.value.code == "ADMISSION_IDENTITY_REQUIRED"
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(AdmitError) as error:
        admission.load_active_deployment_identity(manifest, tmp_path)
    assert error.value.code == "ADMISSION_IDENTITY_INVALID"
    candidate = _candidate()
    candidate["components"]["openpine"]["wheel"]["filename"] = "openpine-5.0.0rc6.whl"
    manifest.write_text(json.dumps(_resign(candidate)), encoding="utf-8")
    with pytest.raises(AdmitError) as error:
        admission.load_active_deployment_identity(manifest, tmp_path)
    assert error.value.code == "WHEEL_IDENTITY_UNAVAILABLE"
    wheel = tmp_path / "openpine-5.0.0rc6.whl"
    wheel.write_bytes(b"not the signed wheel")
    with pytest.raises(AdmitError) as error:
        admission.load_active_deployment_identity(manifest, tmp_path)
    assert error.value.code == "WHEEL_IDENTITIES_MISMATCH"
    candidate["components"]["openpine"]["wheel"]["filename"] = "../elsewhere.whl"
    manifest.write_text(json.dumps(_resign(candidate)), encoding="utf-8")
    with pytest.raises(AdmitError) as error:
        admission.load_active_deployment_identity(manifest, tmp_path)
    assert error.value.code == "ADMISSION_IDENTITY_INVALID"


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("broken.json", b"not-json"),
        ("non-object.json", b"[]"),
        ("missing-id.json", b"{}"),
    ],
)
def test_installed_schema_catalog_rejects_invalid_bytes(tmp_path: Path, monkeypatch, filename: str, contents: bytes):
    import openpine_contracts

    package = tmp_path / "installed-contracts"
    schemas = package / "schemas"
    schemas.mkdir(parents=True)
    (schemas / filename).write_bytes(contents)
    monkeypatch.setattr(openpine_contracts, "__file__", str(package / "__init__.py"))
    with pytest.raises(AdmitError) as error:
        admission.installed_contract_schema_hashes()
    assert error.value.code == "INSTALLED_SCHEMA_INVALID"


def test_installed_schema_catalog_rejects_empty_and_duplicate_ids(tmp_path: Path, monkeypatch):
    import openpine_contracts

    package = tmp_path / "installed-contracts"
    schemas = package / "schemas"
    schemas.mkdir(parents=True)
    monkeypatch.setattr(openpine_contracts, "__file__", str(package / "__init__.py"))
    with pytest.raises(AdmitError) as error:
        admission.installed_contract_schema_hashes()
    assert error.value.code == "INSTALLED_SCHEMA_INVALID"
    (schemas / "one.json").write_text(json.dumps({"$id": "openpine.job.v1"}))
    (schemas / "two.json").write_text(json.dumps({"$id": "openpine.job.v1"}))
    with pytest.raises(AdmitError) as error:
        admission.installed_contract_schema_hashes()
    assert error.value.code == "INSTALLED_SCHEMA_INVALID"
