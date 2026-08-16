"""Fail-closed stack admission. Env drift flags are never honored."""

from __future__ import annotations

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

DEFAULT_STACK_ID = "openpine-5.0"
_BROKER_MODES = frozenset({RunMode.LIVE, RunMode.PAPER})


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


def admit_run(
    *,
    mode: RunMode | str,
    stack_id: str,
    expected_stack_id: str = DEFAULT_STACK_ID,
    profile: str = "prod",
    explicit_override: bool = False,
    artifact_hash: str = "sha256:0",
    expected_artifact_hash: str = "sha256:0",
    required_capabilities: tuple[str, ...] = (),
    capabilities: frozenset[str] | None = None,
) -> AdmitResult:
    run_mode = mode if isinstance(mode, RunMode) else parse_run_mode(mode)
    allow_stack_override = (
        explicit_override and profile == "local-dev" and run_mode not in _BROKER_MODES
    )
    request = AdmitRequest(
        schema_id="openpine.run.v2",
        schema_major=2,
        schema_minor=0,
        required_capabilities=required_capabilities,
        stack_id=stack_id,
        artifact_hash=artifact_hash,
    )
    policy = AdmitPolicy(
        supported_schema_id="openpine.run.v2",
        min_major=2,
        max_major=2,
        min_minor=0,
        max_minor=0,
        capabilities=capabilities if capabilities is not None else frozenset(),
        expected_stack_id=expected_stack_id,
        expected_artifact_hash=expected_artifact_hash,
        allow_stack_override=allow_stack_override,
    )
    return admit(request, policy)


def admit_semantic_profile(
    *,
    profile: object | None,
    source: str,
    allow_legacy: bool = False,
) -> SemanticProfile:
    if profile is None:
        if source in {"generated_artifact.v2", "live", "paper"}:
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


def require_strategy_semantic_profile(strategy: Any) -> SemanticProfile:
    mode = str(getattr(strategy, "mode", "") or "")
    source = mode if mode in {"live", "paper"} else "backtest"
    return admit_semantic_profile(
        profile=getattr(strategy, "semantic_profile", None),
        source=source,
        allow_legacy=bool(getattr(strategy, "allow_legacy", False)),
    )


def require_admitted(**kwargs: Any) -> AdmitResult:
    return admit_run(**kwargs)
