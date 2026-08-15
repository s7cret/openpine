"""Fail-closed stack admission. Env drift flags are never honored."""

from __future__ import annotations

from typing import Any

from openpine_contracts import (
    AdmitError,
    AdmitPolicy,
    AdmitRequest,
    AdmitResult,
    RunMode,
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


def require_admitted(**kwargs: Any) -> AdmitResult:
    return admit_run(**kwargs)
