from __future__ import annotations

from copy import deepcopy

import pytest

from openpine.admission import DeploymentAdmissionIdentity
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import execution_context_from_admission
from openpine_contracts import AdmitError, validate_payload, verify_content_hash


SOURCE = '//@version=6\nindicator("rc6-context")\nplot(close)\n'
COMPONENTS = (
    "openpine-contracts",
    "pine2ast",
    "ast2python",
    "pinelib",
    "marketdata-provider",
    "backtest_engine",
    "optimizer",
    "openpine",
)
COMMITS = {name: f"{index:x}" * 40 for index, name in enumerate(COMPONENTS, start=1)}
STACK_HASH = "sha256:" + "d" * 64


def _artifact_record() -> dict[str, object]:
    result = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="generated_rc6_context",
        source_name="rc6-context.pine",
        producer_commits={
            "pine2ast": COMMITS["pine2ast"],
            "ast2python": COMMITS["ast2python"],
        },
    )
    assert result.success, result.errors
    return {
        "generated_artifact": result.generated_artifact,
        "python_code": result.python_code,
        "consumer_bundle": result.consumer_bundle,
        "source_map": result.source_map,
        "compile_meta": result.compile_meta,
    }


def _deployment() -> DeploymentAdmissionIdentity:
    schema_ids = (
        "openpine.execution_context.v1",
        "openpine.intent.v2",
        "openpine.worker.protocol.v2",
        "openpine.checkpoint.v1",
        "openpine.checkpoint.proof.v1",
    )
    return DeploymentAdmissionIdentity(
        stack_id="openpine-5.0",
        stack_manifest_hash=STACK_HASH,
        wheel_identities=tuple(
            (name, "5.0.0rc6", "sha256:" + f"{index:x}" * 64)
            for index, name in enumerate(COMPONENTS, start=1)
        ),
        schema_hashes={schema_id: "sha256:" + "e" * 64 for schema_id in schema_ids},
        capabilities=frozenset(
            {"closed_bar", "deterministic_clock", "checkpoint_v1", "sealed_artifact_refs"}
        ),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )


def _manifest() -> dict[str, object]:
    return {
        "manifest_hash": STACK_HASH,
        "components": {name: {"sha": COMMITS[name]} for name in COMPONENTS},
    }


def test_execution_context_binds_v3_bundle_and_target_to_admitted_commits() -> None:
    artifact = _artifact_record()
    generated = artifact["generated_artifact"]
    assert isinstance(generated, dict)

    context = execution_context_from_admission(
        _deployment(),
        _manifest(),
        run_id="run-rc6",
        strategy_id="strategy-rc6",
        artifact=artifact,
        data_snapshot_hash="sha256:" + "f" * 64,
        series_id="binance:spot:SOLUSDT:1m",
        instrument_id="binance:spot:SOLUSDT",
        exchange="binance",
        market="spot",
        symbol="SOLUSDT",
        timeframe="1m",
        semantic_profile="strict_5x",
        created_at_utc_ms=0,
    )

    validate_payload("openpine.execution_context.v1", context)
    assert verify_content_hash(context, schema_id="openpine.execution_context.v1")
    assert context["generated_artifact_hash"] == generated["content_hash"]
    assert context["source_hash"] == generated["source_hash"]
    assert context["emitted_module_hash"] == generated["emitted_module_hash"]
    assert context["producer_commits"] == COMMITS
    # Execution-context V1 keeps its stable registry generation identifiers;
    # package RC6 identity is carried by wheel identities and producer commits.
    assert context["policy_registry_version"] == "openpine.policies.rc4.v1"
    assert context["schema_registry_version"] == "openpine.schemas.rc4.v1"
    assert context["capability_registry_version"] == "openpine.capabilities.rc4.v1"


def test_execution_context_rejects_v3_bundle_commit_drift() -> None:
    artifact = deepcopy(_artifact_record())
    bundle = artifact["consumer_bundle"]
    assert isinstance(bundle, dict)
    bundle["producer"]["commit"] = "f" * 40

    with pytest.raises(AdmitError) as error_info:
        execution_context_from_admission(
            _deployment(),
            _manifest(),
            run_id="run-rc6",
            strategy_id="strategy-rc6",
            artifact=artifact,
            data_snapshot_hash="sha256:" + "f" * 64,
            series_id="binance:spot:SOLUSDT:1m",
            instrument_id="binance:spot:SOLUSDT",
            exchange="binance",
            market="spot",
            symbol="SOLUSDT",
            timeframe="1m",
            semantic_profile="strict_5x",
            created_at_utc_ms=0,
        )

    assert error_info.value.code == "GENERATED_ARTIFACT_INVALID"
