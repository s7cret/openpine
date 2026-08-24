"""Exact immutable identity for an admitted execution run."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpine_contracts import (
    AdmitError,
    SchemaValidationError,
    seal_content_hash,
    validate_payload,
    verify_content_hash,
)
from openpine_contracts.hashing import CONTENT_HASH_ALG, SERIALIZER_ID, content_hash

from openpine.admission import (
    DeploymentAdmissionIdentity,
    RunAdmissionIdentity,
    admit_run,
    parse_run_mode,
)
from openpine.build_identity import current_build_identity

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATA_SNAPSHOT_SCHEMA = "openpine.data.snapshot.v1"
_EXECUTION_SCHEMA_IDS = (
    "openpine.execution_context.v1",
    "openpine.intent.v2",
    "openpine.worker.protocol.v2",
    "openpine.checkpoint.v1",
    "openpine.checkpoint.proof.v1",
)
_EXECUTION_CAPABILITIES = frozenset(
    {"closed_bar", "deterministic_clock", "checkpoint_v1", "sealed_artifact_refs"}
)
_GENERATED_ARTIFACT_PRODUCERS = (
    "openpine-contracts",
    "pine2ast",
    "ast2python",
    "pinelib",
)


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        if name not in value:
            raise AdmitError(
                f"data snapshot bar is missing {name}",
                code="DATA_SNAPSHOT_INVALID",
            )
        return value[name]
    if not hasattr(value, name):
        raise AdmitError(
            f"data snapshot bar is missing {name}",
            code="DATA_SNAPSHOT_INVALID",
        )
    return getattr(value, name)


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdmitError(
            f"data snapshot {name} must be an integer",
            code="DATA_SNAPSHOT_INVALID",
        )
    return value


def _number_token(value: object, *, name: str, optional: bool = False) -> object:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise AdmitError(
            f"data snapshot {name} must be numeric",
            code="DATA_SNAPSHOT_INVALID",
        )
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AdmitError(
                f"data snapshot {name} must be finite",
                code="DATA_SNAPSHOT_INVALID",
            )
        return {"type": "float64", "value": value.hex()}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AdmitError(
                f"data snapshot {name} must be finite",
                code="DATA_SNAPSHOT_INVALID",
            )
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, str) and value:
        return {"type": "decimal-string", "value": value}
    raise AdmitError(
        f"data snapshot {name} must be numeric",
        code="DATA_SNAPSHOT_INVALID",
    )


def execution_data_snapshot_hash(
    *,
    bars: Iterable[object],
    exchange: str,
    market: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    finality_policy: str,
    supplemental_bars: Iterable[object] | None = None,
) -> str:
    """Hash every execution-relevant chart and supplemental bar bit."""

    query_strings = {
        "exchange": exchange,
        "market": market,
        "symbol": symbol,
        "timeframe": timeframe,
        "finality_policy": finality_policy,
    }
    if any(not isinstance(value, str) or not value for value in query_strings.values()):
        raise AdmitError(
            "data snapshot query identity is incomplete",
            code="DATA_SNAPSHOT_INVALID",
        )
    start = _integer(start_ms, name="start_ms")
    end = _integer(end_ms, name="end_ms")
    if end < start:
        raise AdmitError(
            "data snapshot range is invalid",
            code="DATA_SNAPSHOT_INVALID",
        )

    def normalize_bar(
        bar: object,
        index: int,
        *,
        label: str,
        require_series_identity: bool,
    ) -> dict[str, object]:
        open_time = _integer(_field(bar, "time"), name=f"{label}[{index}].time")
        close_time = _integer(
            _field(bar, "time_close"), name=f"{label}[{index}].time_close"
        )
        if close_time < open_time:
            raise AdmitError(
                f"data snapshot {label}[{index}] closes before it opens",
                code="DATA_SNAPSHOT_INVALID",
            )
        payload: dict[str, object] = {
            "time": open_time,
            "time_close": close_time,
            "open": _number_token(
                _field(bar, "open"), name=f"{label}[{index}].open"
            ),
            "high": _number_token(
                _field(bar, "high"), name=f"{label}[{index}].high"
            ),
            "low": _number_token(
                _field(bar, "low"), name=f"{label}[{index}].low"
            ),
            "close": _number_token(
                _field(bar, "close"), name=f"{label}[{index}].close"
            ),
            "volume": _number_token(
                _field(bar, "volume"),
                name=f"{label}[{index}].volume",
                optional=True,
            ),
        }
        if require_series_identity:
            series_symbol = _field(bar, "symbol")
            series_timeframe = _field(bar, "timeframe")
            if (
                not isinstance(series_symbol, str)
                or not series_symbol
                or not isinstance(series_timeframe, str)
                or not series_timeframe
            ):
                raise AdmitError(
                    f"data snapshot {label}[{index}] series identity is incomplete",
                    code="DATA_SNAPSHOT_INVALID",
                )
            payload["symbol"] = series_symbol
            payload["timeframe"] = series_timeframe
        return payload

    normalized_bars = [
        normalize_bar(bar, index, label="bars", require_series_identity=False)
        for index, bar in enumerate(bars)
    ]
    if not normalized_bars:
        raise AdmitError(
            "data snapshot bars are required",
            code="DATA_SNAPSHOT_INVALID",
        )
    normalized_supplemental = [
        normalize_bar(
            bar,
            index,
            label="supplemental_bars",
            require_series_identity=True,
        )
        for index, bar in enumerate(supplemental_bars or ())
    ]

    return content_hash(
        {
            "query": {
                **query_strings,
                "start_ms": start,
                "end_ms": end,
            },
            "bars": normalized_bars,
            "supplemental_bars": normalized_supplemental,
        },
        schema_id=_DATA_SNAPSHOT_SCHEMA,
    )


def generated_artifact_hash(artifact: Mapping[str, object]) -> str:
    envelope = artifact.get("generated_artifact")
    if not isinstance(envelope, Mapping):
        raise AdmitError(
            "sealed generated artifact is required",
            code="GENERATED_ARTIFACT_IDENTITY_REQUIRED",
        )
    try:
        validate_payload("openpine.generated_artifact.v2", envelope)
    except SchemaValidationError as exc:
        raise AdmitError(
            "generated artifact schema is invalid",
            code="GENERATED_ARTIFACT_IDENTITY_INVALID",
        ) from exc
    if not verify_content_hash(envelope, schema_id="openpine.generated_artifact.v2"):
        raise AdmitError(
            "generated artifact content hash is invalid",
            code="GENERATED_ARTIFACT_HASH_MISMATCH",
        )
    value = envelope.get("content_hash")
    if not isinstance(value, str):
        raise AdmitError(
            "generated artifact content hash is missing",
            code="GENERATED_ARTIFACT_HASH_MISMATCH",
        )
    return value


def verified_generated_source(artifact: Mapping[str, object]) -> bytes:
    """Return the exact emitted module bytes bound by the sealed envelope."""

    generated_artifact_hash(artifact)
    source = artifact.get("python_code")
    if not isinstance(source, str) or not source:
        raise AdmitError(
            "sealed generated artifact Python source is required",
            code="GENERATED_ARTIFACT_IDENTITY_REQUIRED",
        )
    envelope = artifact["generated_artifact"]
    assert isinstance(envelope, Mapping)
    from ast2python.artifact import _digest

    expected = envelope.get("emitted_module_hash")
    actual = _digest(source, "openpine.generated_artifact.v2")
    if expected != actual:
        raise AdmitError(
            "generated artifact emitted module hash mismatch",
            code="GENERATED_ARTIFACT_HASH_MISMATCH",
        )
    return source.encode("utf-8")


def run_identity_path(data_dir: str | Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise AdmitError("run_id is invalid", code="RUN_IDENTITY_INVALID")
    return Path(data_dir) / "run-identities" / f"{run_id}.json"


def persist_run_identity(
    data_dir: str | Path,
    run_id: str,
    payload: Mapping[str, object],
) -> Path:
    """Durably publish immutable identity bytes before execution begins."""

    run_identity_path(data_dir, run_id)
    raw_root = Path(data_dir)
    try:
        raw_root.mkdir(parents=True, exist_ok=True)
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise AdmitError(
            "run identity data directory is unavailable",
            code="RUN_IDENTITY_INVALID",
        ) from exc
    if not root.is_dir():
        raise AdmitError(
            "run identity data directory is invalid",
            code="RUN_IDENTITY_INVALID",
        )
    filename = f"{run_id}.json"
    path = root / "run-identities" / filename
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    missing = object()

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_fd: int | None = None
    try:
        try:
            directory_info = os.stat(
                "run-identities", dir_fd=root_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            try:
                os.mkdir("run-identities", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            else:
                os.fsync(root_fd)
            directory_info = os.stat(
                "run-identities", dir_fd=root_fd, follow_symlinks=False
            )
        if stat.S_ISLNK(directory_info.st_mode):
            raise AdmitError(
                "run identity directory symlink is forbidden",
                code="RUN_IDENTITY_INVALID",
            )
        if not stat.S_ISDIR(directory_info.st_mode):
            raise AdmitError(
                "run identity directory is invalid",
                code="RUN_IDENTITY_INVALID",
            )
        try:
            directory_fd = os.open(
                "run-identities",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except OSError as exc:
            raise AdmitError(
                "run identity directory symlink is forbidden",
                code="RUN_IDENTITY_INVALID",
            ) from exc

        def read_existing() -> object:
            try:
                descriptor = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return missing
            except OSError as exc:
                raise AdmitError(
                    "run identity target symlink is forbidden",
                    code="RUN_IDENTITY_INVALID",
                ) from exc
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise AdmitError(
                        "run identity target must be a regular file",
                        code="RUN_IDENTITY_INVALID",
                    )
                with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                    descriptor = -1
                    return json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise AdmitError(
                    "persisted run identity is corrupt",
                    code="RUN_IDENTITY_CONFLICT",
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

        def require_existing_match(existing: object) -> Path:
            if existing == dict(payload):
                os.fsync(directory_fd)
                return path
            raise AdmitError(
                "conflicting persisted run identity",
                code="RUN_IDENTITY_CONFLICT",
            )

        existing = read_existing()
        if existing is not missing:
            return require_existing_match(existing)

        temp_name = f".{run_id}.{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(
                    temp_name,
                    filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return require_existing_match(read_existing())
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        return path
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(root_fd)


def _run_payload(
    *,
    deployment: DeploymentAdmissionIdentity,
    run: RunAdmissionIdentity,
    mode: object,
    run_id: str,
    created_at_utc_ms: int,
) -> dict[str, object]:
    build = current_build_identity()
    run_mode = parse_run_mode(mode)
    payload: dict[str, object] = {
        "schema_id": "openpine.run.v2",
        "schema_version": "2.1.0",
        "producer": "openpine",
        "producer_version": build.contract_version,
        "producer_commit": build.commit,
        "stack_id": deployment.stack_id,
        "created_at_utc_ms": _integer(
            created_at_utc_ms, name="created_at_utc_ms"
        ),
        "serializer_id": SERIALIZER_ID,
        "content_hash_alg": CONTENT_HASH_ALG,
        "run_id": run_id,
        "run_mode": run_mode.value,
        "state": "ADMITTED",
        "stack_manifest_hash": run.stack_manifest_hash,
        "wheel_identities": [
            {"name": name, "version": version, "content_hash": digest}
            for name, version, digest in run.wheel_identities
        ],
        "schema_hashes": dict(run.schema_hashes),
        "generated_artifact_hash": run.generated_artifact_hash,
        "data_snapshot_hash": run.data_snapshot_hash,
        "semantic_profile": run.semantic_profile,
        "finality_policy": run.finality_policy,
        "warmup_policy": run.warmup_policy,
        "score_policy": run.score_policy,
        "required_capabilities": list(run.required_capabilities),
    }
    sealed = seal_content_hash(payload, schema_id="openpine.run.v2")
    validate_payload("openpine.run.v2", sealed)
    return sealed


def admit_and_persist_run_identity(
    *,
    data_dir: str | Path,
    deployment: DeploymentAdmissionIdentity,
    admitted_manifest: Mapping[str, object],
    mode: object,
    run_id: str,
    artifact: Mapping[str, object],
    bars: Iterable[object],
    supplemental_bars: Iterable[object] | None = None,
    exchange: str,
    market: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    semantic_profile: str,
    finality_policy: str,
    warmup_policy: str,
    score_policy: str,
    required_capabilities: tuple[str, ...],
    created_at_utc_ms: int,
) -> dict[str, object]:
    if admitted_manifest.get("manifest_hash") != deployment.stack_manifest_hash:
        raise AdmitError(
            "admitted manifest does not match deployment identity",
            code="ADMISSION_IDENTITY_INVALID",
        )
    components = admitted_manifest.get("components")
    openpine_component = (
        components.get("openpine") if isinstance(components, Mapping) else None
    )
    expected_producer_commit = (
        openpine_component.get("sha")
        if isinstance(openpine_component, Mapping)
        else None
    )
    if (
        not isinstance(expected_producer_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", expected_producer_commit) is None
    ):
        raise AdmitError(
            "admitted OpenPine producer commit is required",
            code="ADMISSION_IDENTITY_INVALID",
        )
    artifact_hash = generated_artifact_hash(artifact)
    snapshot_hash = execution_data_snapshot_hash(
        bars=bars,
        supplemental_bars=supplemental_bars,
        exchange=exchange,
        market=market,
        symbol=symbol,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        finality_policy=finality_policy,
    )
    run = RunAdmissionIdentity(
        stack_manifest_hash=deployment.stack_manifest_hash,
        wheel_identities=deployment.wheel_identities,
        schema_hashes=deployment.schema_hashes,
        generated_artifact_hash=artifact_hash,
        data_snapshot_hash=snapshot_hash,
        semantic_profile=semantic_profile,
        finality_policy=finality_policy,
        warmup_policy=warmup_policy,
        score_policy=score_policy,
        required_capabilities=required_capabilities,
    )
    admit_run(
        mode=mode,
        deployment=deployment,
        run=run,
        expected_artifact_hash=artifact_hash,
        expected_data_snapshot_hash=snapshot_hash,
    )
    payload = _run_payload(
        deployment=deployment,
        run=run,
        mode=mode,
        run_id=run_id,
        created_at_utc_ms=created_at_utc_ms,
    )
    if payload.get("producer_commit") != expected_producer_commit:
        raise AdmitError(
            "run producer commit differs from admitted OpenPine commit",
            code="ADMISSION_IDENTITY_INVALID",
        )
    persist_run_identity(data_dir, run_id, payload)
    return payload


def derive_execution_context(
    base_context: Mapping[str, object],
    *,
    run_id: str,
    strategy_id: str,
    artifact: Mapping[str, object],
    data_snapshot_hash: str,
    series_id: str,
    instrument_id: str,
    exchange: str,
    market: str,
    symbol: str,
    timeframe: str,
    semantic_profile: str,
) -> dict[str, object]:
    """Bind an admitted stack template to one exact execution run."""

    validate_payload("openpine.execution_context.v1", base_context)
    if not verify_content_hash(
        base_context, schema_id="openpine.execution_context.v1"
    ):
        raise AdmitError(
            "base execution context content hash is invalid",
            code="EXECUTION_CONTEXT_INVALID",
        )
    generated = artifact.get("generated_artifact")
    if not isinstance(generated, Mapping):
        raise AdmitError(
            "generated artifact envelope is required",
            code="GENERATED_ARTIFACT_REQUIRED",
        )
    validate_payload("openpine.generated_artifact.v2", generated)
    if not verify_content_hash(generated, schema_id="openpine.generated_artifact.v2"):
        raise AdmitError(
            "generated artifact content hash is invalid",
            code="GENERATED_ARTIFACT_INVALID",
        )
    payload = dict(base_context)
    payload.pop("content_hash", None)
    payload.update(
        {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "session_id": f"{run_id}:worker",
            "generated_artifact_hash": generated["content_hash"],
            "source_hash": generated["source_hash"],
            "emitted_module_hash": generated["emitted_module_hash"],
            "data_snapshot_hash": data_snapshot_hash,
            "series_id": series_id,
            "instrument_id": instrument_id,
            "exchange": exchange,
            "market": market,
            "symbol": symbol,
            "timeframe": timeframe,
            "semantic_profile": semantic_profile,
        }
    )
    sealed = seal_content_hash(payload, schema_id="openpine.execution_context.v1")
    validate_payload("openpine.execution_context.v1", sealed)
    return sealed


def _protocol_semver(value: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", value)
    return f"{match.group(1)}-rc.{match.group(2)}" if match else value


def execution_context_from_admission(
    deployment: DeploymentAdmissionIdentity,
    admitted_manifest: Mapping[str, object],
    *,
    run_id: str,
    strategy_id: str,
    artifact: Mapping[str, object],
    data_snapshot_hash: str,
    series_id: str,
    instrument_id: str,
    exchange: str,
    market: str,
    symbol: str,
    timeframe: str,
    semantic_profile: str,
    created_at_utc_ms: int,
    timezone: str = "UTC",
    currency: str = "USD",
    mintick: str = "0.01",
    pointvalue: str = "1",
    session_policy: str = "24x7",
) -> dict[str, object]:
    """Construct one sealed execution context from exact deployment evidence."""

    if admitted_manifest.get("manifest_hash") != deployment.stack_manifest_hash:
        raise AdmitError(
            "admitted manifest does not match deployment identity",
            code="STACK_MANIFEST_HASH_MISMATCH",
        )
    generated = artifact.get("generated_artifact")
    if not isinstance(generated, Mapping):
        raise AdmitError(
            "generated artifact envelope is required",
            code="GENERATED_ARTIFACT_REQUIRED",
        )
    validate_payload("openpine.generated_artifact.v2", generated)
    if not verify_content_hash(generated, schema_id="openpine.generated_artifact.v2"):
        raise AdmitError(
            "generated artifact content hash is invalid",
            code="GENERATED_ARTIFACT_INVALID",
        )
    components = admitted_manifest.get("components")
    if not isinstance(components, Mapping):
        raise AdmitError(
            "admitted component commits are required",
            code="ADMISSION_IDENTITY_INVALID",
        )
    wheel_identities = [
        {"name": name, "version": version, "content_hash": digest}
        for name, version, digest in deployment.wheel_identities
    ]
    if len(wheel_identities) != 8:
        raise AdmitError(
            "exactly eight wheel identities are required",
            code="ADMISSION_IDENTITY_INVALID",
        )
    producer_commits: dict[str, str] = {}
    for row in wheel_identities:
        name = str(row["name"])
        component = components.get(name)
        if not isinstance(component, Mapping):
            raise AdmitError(
                f"admitted component identity is missing: {name}",
                code="ADMISSION_IDENTITY_INVALID",
            )
        commit = component.get("sha", component.get("commit"))
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise AdmitError(
                f"admitted component commit is invalid: {name}",
                code="ADMISSION_IDENTITY_INVALID",
            )
        producer_commits[name] = commit
    generated_commits = generated.get("producer_commits")
    if not isinstance(generated_commits, Mapping):
        raise AdmitError(
            "generated artifact producer commits are required",
            code="GENERATED_ARTIFACT_INVALID",
        )
    for component_name in _GENERATED_ARTIFACT_PRODUCERS:
        if generated_commits.get(component_name) != producer_commits[component_name]:
            raise AdmitError(
                f"generated artifact producer commit drift: {component_name}",
                code="GENERATED_ARTIFACT_INVALID",
            )
    if generated.get("producer_commit") != producer_commits["ast2python"]:
        raise AdmitError(
            "generated artifact producer commit drift: ast2python",
            code="GENERATED_ARTIFACT_INVALID",
        )
    if generated.get("stack_id") != "openpine-5.0":
        raise AdmitError(
            "generated artifact stack family is invalid",
            code="GENERATED_ARTIFACT_INVALID",
        )
    versions = {str(row["name"]): str(row["version"]) for row in wheel_identities}
    required_schema_hashes: dict[str, str] = {}
    for schema_id in _EXECUTION_SCHEMA_IDS:
        digest = deployment.schema_hashes.get(schema_id)
        if not isinstance(digest, str):
            raise AdmitError(
                f"execution schema hash is missing: {schema_id}",
                code="ADMISSION_IDENTITY_INVALID",
            )
        required_schema_hashes[schema_id] = digest
    payload: dict[str, object] = {
        "schema_id": "openpine.execution_context.v1",
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": _protocol_semver(versions["openpine"]),
        "producer_commit": producer_commits["openpine"],
        "stack_id": deployment.stack_manifest_hash,
        "created_at_utc_ms": int(created_at_utc_ms),
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "run_id": run_id,
        "strategy_id": strategy_id,
        "session_id": f"{run_id}:worker",
        "stack_manifest_hash": deployment.stack_manifest_hash,
        "wheel_identities": wheel_identities,
        "schema_hashes": required_schema_hashes,
        "generated_artifact_hash": generated["content_hash"],
        "source_hash": generated["source_hash"],
        "emitted_module_hash": generated["emitted_module_hash"],
        "data_snapshot_hash": data_snapshot_hash,
        "series_id": series_id,
        "instrument_id": instrument_id,
        "exchange": exchange,
        "market": market,
        "symbol": symbol,
        "timeframe": timeframe,
        "timezone": timezone,
        "currency": currency,
        "mintick": mintick,
        "pointvalue": pointvalue,
        "session_policy": session_policy,
        "semantic_profile": semantic_profile,
        "finality_policy": "CLOSED_BAR_ONLY",
        "warmup_policy": "CALC_ONLY",
        "score_policy": "ALL_BARS",
        "end_policy": "LIQUIDATE_ON_LAST_BAR",
        "capabilities": sorted(deployment.capabilities & _EXECUTION_CAPABILITIES),
        "producer_commits": producer_commits,
        "policy_registry_version": "openpine.policies.rc4.v1",
        "schema_registry_version": "openpine.schemas.rc4.v1",
        "capability_registry_version": "openpine.capabilities.rc4.v1",
    }
    sealed = seal_content_hash(payload, schema_id="openpine.execution_context.v1")
    validate_payload("openpine.execution_context.v1", sealed)
    return sealed


def bind_isolated_execution(
    config: object,
    *,
    data_dir: str | Path,
    deployment: DeploymentAdmissionIdentity,
    admitted_manifest: Mapping[str, object],
    mode: object,
    run_id: str,
    strategy_id: str,
    artifact: Mapping[str, object],
    bars: Iterable[object],
    bar_envelopes: Iterable[Mapping[str, object]],
    supplemental_bars: Iterable[object] | None,
    created_at_utc_ms: int,
) -> dict[str, object]:
    """Persist one admitted run and attach exact protocol inputs to its config."""

    bar_list = list(bars)
    envelopes = [dict(item) for item in bar_envelopes]
    if not bar_list or len(envelopes) != len(bar_list):
        raise AdmitError(
            "exact canonical bar envelopes are required",
            code="CANONICAL_BAR_ENVELOPES_REQUIRED",
        )
    run_identity = admit_and_persist_run_identity(
        data_dir=data_dir,
        deployment=deployment,
        admitted_manifest=admitted_manifest,
        mode=mode,
        run_id=run_id,
        artifact=artifact,
        bars=bar_list,
        supplemental_bars=supplemental_bars,
        exchange=str(getattr(config, "exchange")),
        market=str(getattr(config, "market_type")),
        symbol=str(getattr(config, "symbol")),
        timeframe=str(getattr(config, "timeframe")),
        start_ms=int(getattr(config, "start_time")),
        end_ms=int(getattr(config, "end_time")),
        semantic_profile=str(getattr(config, "semantic_profile")),
        finality_policy="CLOSED_BAR_ONLY",
        warmup_policy="CALC_ONLY",
        score_policy="ALL_BARS",
        required_capabilities=(
            "closed_bar",
            "deterministic_clock",
            "isolated_worker",
            "broker_projection",
            "intent_tape_v2",
        ),
        created_at_utc_ms=created_at_utc_ms,
    )
    first = envelopes[0]
    execution_context = execution_context_from_admission(
        deployment,
        admitted_manifest,
        run_id=run_id,
        strategy_id=strategy_id,
        artifact=artifact,
        data_snapshot_hash=str(run_identity["data_snapshot_hash"]),
        series_id=str(first["series_id"]),
        instrument_id=str(first["instrument_id"]),
        exchange=str(getattr(config, "exchange")),
        market=str(getattr(config, "market_type")),
        symbol=str(getattr(config, "symbol")),
        timeframe=str(getattr(config, "timeframe")),
        semantic_profile=str(getattr(config, "semantic_profile")),
        created_at_utc_ms=created_at_utc_ms,
    )
    generated = artifact.get("generated_artifact")
    assert isinstance(generated, Mapping)
    for name, value in (
        ("execution_context", execution_context),
        ("admitted_manifest", dict(admitted_manifest)),
        ("instrument_id", execution_context["instrument_id"]),
        ("generated_artifact", dict(generated)),
        ("bar_envelopes", envelopes),
        ("run_hash", str(run_identity["content_hash"])),
        ("protocol_artifact_dir", str(Path(data_dir) / "protocol" / run_id)),
    ):
        object.__setattr__(config, name, value)
    return run_identity
