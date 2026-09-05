"""Fail-closed stack admission. Env drift flags are never honored."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpine_contracts import (
    AdmitError,
    AdmitPolicy,
    AdmitRequest,
    AdmitResult,
    RunMode,
    SemanticProfile,
    admit,
)

_BROKER_MODES = frozenset({RunMode.LIVE, RunMode.PAPER})
_HASH = re.compile(r"^sha256:(?!0{64}$)[0-9a-f]{64}$")
_CANDIDATE_SCHEMA = "openpine.stack-candidate.v2"
WheelIdentity = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class DeploymentAdmissionIdentity:
    stack_id: str
    stack_manifest_hash: str
    wheel_identities: tuple[WheelIdentity, ...]
    schema_hashes: Mapping[str, str]
    capabilities: frozenset[str]
    semantic_profiles: frozenset[str]
    finality_policies: frozenset[str]
    warmup_policies: frozenset[str]
    score_policies: frozenset[str]


@dataclass(frozen=True, slots=True)
class RunAdmissionIdentity:
    stack_manifest_hash: str
    wheel_identities: tuple[WheelIdentity, ...]
    schema_hashes: Mapping[str, str]
    generated_artifact_hash: str
    data_snapshot_hash: str
    semantic_profile: str
    finality_policy: str
    warmup_policy: str
    score_policy: str
    required_capabilities: tuple[str, ...] = ()


def candidate_manifest_hash(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("manifest_hash", None)
    encoded = json.dumps(
        {"domain": _CANDIDATE_SCHEMA, "payload": unsigned},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_hash(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise AdmitError(
            f"{label} must be a nonzero SHA-256",
            code="ADMISSION_IDENTITY_INVALID",
            details={"field": label},
        )
    return value


def _require_strings(value: object, *, label: str) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise AdmitError(
            f"{label} must be a non-empty string list",
            code="ADMISSION_POLICY_INVALID",
            details={"field": label},
        )
    return frozenset(value)


def deployment_identity_from_candidate(
    candidate: Mapping[str, Any],
) -> DeploymentAdmissionIdentity:
    if candidate.get("schema") != _CANDIDATE_SCHEMA:
        raise AdmitError(
            "candidate schema is not supported",
            code="ADMISSION_IDENTITY_INVALID",
        )
    if candidate.get("stage") != "wheel-bound":
        raise AdmitError(
            "wheel-bound candidate required for admission",
            code="ADMISSION_IDENTITY_INVALID",
        )
    stack_id = candidate.get("id")
    if not isinstance(stack_id, str) or not stack_id:
        raise AdmitError("candidate id is required", code="ADMISSION_IDENTITY_INVALID")
    manifest_hash = _require_hash(
        candidate.get("manifest_hash"), label="candidate manifest hash"
    )
    if manifest_hash != candidate_manifest_hash(candidate):
        raise AdmitError(
            "candidate manifest hash mismatch",
            code="STACK_MANIFEST_HASH_MISMATCH",
        )

    components = candidate.get("components")
    if not isinstance(components, Mapping) or not components:
        raise AdmitError("candidate wheels are required", code="ADMISSION_IDENTITY_INVALID")
    wheels: list[WheelIdentity] = []
    for name in sorted(components):
        row = components[name]
        if not isinstance(name, str) or not isinstance(row, Mapping):
            raise AdmitError("candidate wheel identity is invalid", code="ADMISSION_IDENTITY_INVALID")
        version = row.get("version")
        wheel = row.get("wheel")
        if not isinstance(version, str) or not version or not isinstance(wheel, Mapping):
            raise AdmitError(
                f"candidate wheel identity is invalid: {name}",
                code="ADMISSION_IDENTITY_INVALID",
            )
        wheels.append(
            (
                name,
                version,
                _require_hash(wheel.get("sha256"), label=f"{name} wheel hash"),
            )
        )

    raw_schema_hashes = candidate.get("schema_hashes")
    if not isinstance(raw_schema_hashes, Mapping) or not raw_schema_hashes:
        raise AdmitError(
            "candidate schema hashes are required",
            code="ADMISSION_IDENTITY_INVALID",
        )
    schema_hashes: dict[str, str] = {}
    for schema_id, digest in raw_schema_hashes.items():
        if not isinstance(schema_id, str) or not schema_id:
            raise AdmitError("candidate schema hashes are invalid", code="ADMISSION_IDENTITY_INVALID")
        schema_hashes[schema_id] = _require_hash(
            digest, label=f"schema hash {schema_id}"
        )

    raw_admission = candidate.get("admission")
    if not isinstance(raw_admission, Mapping):
        raise AdmitError(
            "candidate admission policy is required",
            code="ADMISSION_POLICY_INVALID",
        )
    return DeploymentAdmissionIdentity(
        stack_id=stack_id,
        stack_manifest_hash=manifest_hash,
        wheel_identities=tuple(wheels),
        schema_hashes=schema_hashes,
        capabilities=_require_strings(
            raw_admission.get("capabilities"), label="capabilities"
        ),
        semantic_profiles=_require_strings(
            raw_admission.get("semantic_profiles"), label="semantic profiles"
        ),
        finality_policies=_require_strings(
            raw_admission.get("finality_policies"), label="finality policies"
        ),
        warmup_policies=_require_strings(
            raw_admission.get("warmup_policies"), label="warmup policies"
        ),
        score_policies=_require_strings(
            raw_admission.get("score_policies"), label="score policies"
        ),
    )


def installed_contract_schema_hashes() -> dict[str, str]:
    import openpine_contracts

    module_file = getattr(openpine_contracts, "__file__", None)
    if not isinstance(module_file, str) or not module_file:
        raise AdmitError(
            "installed contracts package path is unavailable",
            code="INSTALLED_SCHEMA_INVALID",
        )
    root = Path(module_file).resolve().parent / "schemas"
    output: dict[str, str] = {}
    for path in sorted(root.glob("*.json")):
        content = path.read_bytes()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AdmitError(
                f"installed contract schema is invalid: {path.name}",
                code="INSTALLED_SCHEMA_INVALID",
            ) from exc
        schema_id = payload.get("$id") if isinstance(payload, dict) else None
        if not isinstance(schema_id, str) or not schema_id or schema_id in output:
            raise AdmitError(
                f"installed contract schema identity is invalid: {path.name}",
                code="INSTALLED_SCHEMA_INVALID",
            )
        output[schema_id] = "sha256:" + hashlib.sha256(content).hexdigest()
    if not output:
        raise AdmitError(
            "installed contract schema hashes are unavailable",
            code="INSTALLED_SCHEMA_INVALID",
        )
    return output


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AdmitError(
            f"candidate wheel is unavailable: {path.name}",
            code="WHEEL_IDENTITY_UNAVAILABLE",
        ) from exc
    return "sha256:" + digest.hexdigest()


def load_active_deployment_identity(
    manifest_path: Path,
    wheelhouse: Path,
    *,
    version_reader: Callable[[str], str] | None = None,
    schema_hashes_reader: Callable[[], Mapping[str, str]] | None = None,
) -> DeploymentAdmissionIdentity:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmitError(
            "active candidate manifest is unavailable",
            code="ADMISSION_IDENTITY_REQUIRED",
        ) from exc
    if not isinstance(payload, Mapping):
        raise AdmitError(
            "active candidate manifest is invalid",
            code="ADMISSION_IDENTITY_INVALID",
        )
    deployment = deployment_identity_from_candidate(payload)
    components = payload["components"]
    assert isinstance(components, Mapping)
    read_version = version_reader or importlib.metadata.version
    for name, row in components.items():
        assert isinstance(name, str) and isinstance(row, Mapping)
        wheel = row["wheel"]
        assert isinstance(wheel, Mapping)
        filename = wheel.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
        ):
            raise AdmitError(
                f"candidate wheel filename is invalid: {name}",
                code="ADMISSION_IDENTITY_INVALID",
            )
        expected_hash = _require_hash(wheel.get("sha256"), label=f"{name} wheel hash")
        if _file_hash(wheelhouse / filename) != expected_hash:
            raise AdmitError(
                f"wheel hash mismatch: {name}",
                code="WHEEL_IDENTITIES_MISMATCH",
            )
        expected_version = str(row.get("version") or "")
        distribution = name.replace("_", "-")
        try:
            installed_version = read_version(distribution)
        except Exception as exc:
            raise AdmitError(
                f"installed version unavailable: {distribution}",
                code="INSTALLED_VERSION_MISMATCH",
            ) from exc
        if installed_version != expected_version:
            raise AdmitError(
                f"installed version mismatch: {distribution}",
                code="INSTALLED_VERSION_MISMATCH",
                details={"installed": installed_version, "expected": expected_version},
            )
    read_schemas = schema_hashes_reader or installed_contract_schema_hashes
    installed_schemas = _normalize_schema_hashes(read_schemas())
    if installed_schemas != _normalize_schema_hashes(deployment.schema_hashes):
        raise AdmitError(
            "installed schema hashes mismatch",
            code="SCHEMA_HASHES_MISMATCH",
        )
    return deployment


def admit_configured_deployment(*, mode: RunMode | str) -> AdmitResult:
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    if config.deployment_manifest is None or config.deployment_wheelhouse is None:
        raise AdmitError(
            "active deployment identity is required",
            code="ADMISSION_IDENTITY_REQUIRED",
        )
    deployment = load_active_deployment_identity(
        config.deployment_manifest,
        config.deployment_wheelhouse,
    )
    return admit_deployment(mode=mode, deployment=deployment)


def parse_run_mode(value: object) -> RunMode:
    text = str(value).strip()
    try:
        return RunMode(text)
    except ValueError:
        try:
            return RunMode(text.upper())
        except ValueError as exc:
            raise AdmitError(
                "unknown run mode",
                code="UNKNOWN_RUN_MODE",
                details={"mode": text},
            ) from exc


def _normalize_wheels(value: tuple[WheelIdentity, ...]) -> tuple[WheelIdentity, ...]:
    normalized: list[WheelIdentity] = []
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 3
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], str)
            or not item[1]
        ):
            raise AdmitError("wheel identities are invalid", code="ADMISSION_IDENTITY_INVALID")
        normalized.append((item[0], item[1], _require_hash(item[2], label=f"{item[0]} wheel hash")))
    if not normalized:
        raise AdmitError("wheel identities are required", code="ADMISSION_IDENTITY_INVALID")
    return tuple(sorted(normalized))


def _normalize_schema_hashes(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise AdmitError("schema hashes are required", code="ADMISSION_IDENTITY_INVALID")
    output: dict[str, str] = {}
    for schema_id, digest in value.items():
        if not isinstance(schema_id, str) or not schema_id:
            raise AdmitError("schema hashes are invalid", code="ADMISSION_IDENTITY_INVALID")
        output[schema_id] = _require_hash(digest, label=f"schema hash {schema_id}")
    return output


def _require_policy(value: str, allowed: frozenset[str], *, label: str) -> None:
    if value not in allowed:
        raise AdmitError(
            f"{label} is not admitted: {value}",
            code="ADMISSION_POLICY_MISMATCH",
            details={"field": label, "value": value, "allowed": sorted(allowed)},
        )


def admit_deployment(
    *,
    mode: RunMode | str,
    deployment: DeploymentAdmissionIdentity,
    required_capabilities: tuple[str, ...] = (),
) -> AdmitResult:
    run_mode = mode if isinstance(mode, RunMode) else parse_run_mode(mode)
    if not isinstance(deployment, DeploymentAdmissionIdentity):
        raise AdmitError(
            "active deployment identity is required",
            code="ADMISSION_IDENTITY_REQUIRED",
        )
    _require_hash(deployment.stack_manifest_hash, label="candidate manifest hash")
    _normalize_wheels(deployment.wheel_identities)
    _normalize_schema_hashes(deployment.schema_hashes)
    missing = sorted(set(required_capabilities) - deployment.capabilities)
    if missing:
        raise AdmitError(
            f"missing capabilities: {','.join(missing)}",
            code="MISSING_CAPABILITIES",
            details={"missing": missing},
        )
    return AdmitResult(
        True,
        "ADMIT_OK",
        "admitted",
        {
            "mode": str(getattr(run_mode, "value", run_mode)),
            "stack_id": deployment.stack_id,
            "stack_manifest_hash": deployment.stack_manifest_hash,
        },
    )


def admit_run(
    *,
    mode: RunMode | str,
    deployment: DeploymentAdmissionIdentity,
    run: RunAdmissionIdentity,
    expected_artifact_hash: str,
    expected_data_snapshot_hash: str,
    profile: str = "prod",
    explicit_override: bool = False,
) -> AdmitResult:
    run_mode = mode if isinstance(mode, RunMode) else parse_run_mode(mode)
    if not isinstance(run, RunAdmissionIdentity):
        raise AdmitError("run identity is required", code="ADMISSION_IDENTITY_REQUIRED")
    admit_deployment(
        mode=run_mode,
        deployment=deployment,
        required_capabilities=run.required_capabilities,
    )
    allow_stack_override = (
        explicit_override and profile == "local-dev" and run_mode not in _BROKER_MODES
    )
    request = AdmitRequest(
        schema_id="openpine.run.v2",
        schema_major=2,
        schema_minor=1,
        required_capabilities=run.required_capabilities,
        stack_id=_require_hash(run.stack_manifest_hash, label="run stack manifest hash"),
        artifact_hash=_require_hash(
            run.generated_artifact_hash, label="generated artifact hash"
        ),
    )
    policy = AdmitPolicy(
        supported_schema_id="openpine.run.v2",
        min_major=2,
        max_major=2,
        min_minor=1,
        max_minor=1,
        capabilities=deployment.capabilities,
        expected_stack_id=deployment.stack_manifest_hash,
        expected_artifact_hash=_require_hash(
            expected_artifact_hash, label="expected generated artifact hash"
        ),
        allow_stack_override=allow_stack_override,
    )
    result = admit(request, policy)

    if _normalize_wheels(run.wheel_identities) != _normalize_wheels(
        deployment.wheel_identities
    ):
        raise AdmitError(
            "wheel identities mismatch",
            code="WHEEL_IDENTITIES_MISMATCH",
        )
    if _normalize_schema_hashes(run.schema_hashes) != _normalize_schema_hashes(
        deployment.schema_hashes
    ):
        raise AdmitError("schema hashes mismatch", code="SCHEMA_HASHES_MISMATCH")
    snapshot_hash = _require_hash(run.data_snapshot_hash, label="data snapshot hash")
    if snapshot_hash != _require_hash(
        expected_data_snapshot_hash, label="expected data snapshot hash"
    ):
        raise AdmitError(
            "data snapshot hash mismatch",
            code="DATA_SNAPSHOT_HASH_MISMATCH",
        )
    _require_policy(
        run.semantic_profile, deployment.semantic_profiles, label="semantic profile"
    )
    _require_policy(
        run.finality_policy, deployment.finality_policies, label="finality policy"
    )
    _require_policy(run.warmup_policy, deployment.warmup_policies, label="warmup policy")
    _require_policy(run.score_policy, deployment.score_policies, label="score policy")
    return result


def admit_semantic_profile(
    *,
    profile: object | None,
    source: str,
    allow_legacy: bool = False,
) -> SemanticProfile:
    if profile is None:
        if source in {"generated_artifact.v3", "live", "paper"}:
            raise AdmitError(
                "semantic profile required",
                code="SEMANTIC_PROFILE_REQUIRED",
                details={"source": source},
            )
        return SemanticProfile.LEGACY_4X
    try:
        resolved = (
            profile
            if isinstance(profile, SemanticProfile)
            else SemanticProfile(str(profile))
        )
    except ValueError as exc:
        raise AdmitError(
            "unknown semantic profile",
            code="UNKNOWN_SEMANTIC_PROFILE",
            details={"profile": str(profile)},
        ) from exc
    if (
        resolved is SemanticProfile.LEGACY_4X
        and source in {"live", "paper"}
        and not allow_legacy
    ):
        raise AdmitError(
            "legacy semantic profile is not allowed without explicit policy",
            code="LEGACY_PROFILE_NOT_ALLOWED",
            details={"source": source},
        )
    return resolved


CURRENT_SEMANTIC_PROFILE = SemanticProfile.STRICT_5X.value


def admit_strategy_semantic_profile(
    strategy: Any,
    *,
    source: str,
    requested_profile: object | None = None,
) -> SemanticProfile:
    """Admit the immutable semantic stamp stored on a strategy."""

    stored_profile = getattr(strategy, "semantic_profile", None)
    if requested_profile is not None and str(requested_profile) != str(stored_profile):
        raise AdmitError(
            "semantic profile is immutable for strategy",
            code="SEMANTIC_PROFILE_MISMATCH",
            details={"requested": str(requested_profile), "stored": str(stored_profile)},
        )
    return admit_semantic_profile(
        profile=stored_profile,
        source=source,
        allow_legacy=str(stored_profile) == SemanticProfile.LEGACY_4X.value,
    )


def require_strategy_semantic_profile(strategy: Any) -> SemanticProfile:
    mode = str(getattr(strategy, "mode", "") or "")
    source = mode if mode in {"live", "paper"} else "backtest"
    return admit_strategy_semantic_profile(strategy, source=source)


def require_admitted(**kwargs: Any) -> AdmitResult:
    return admit_run(**kwargs)
