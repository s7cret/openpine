from __future__ import annotations

from ast2python.artifact import _digest
from openpine.admission import DeploymentAdmissionIdentity
from openpine_contracts import seal_content_hash

STACK_HASH = "sha256:" + "d" * 64
_FAKE_COMMITS = {
    "pine2ast": "b" * 40,
    "ast2python": "c" * 40,
    "pinelib": "d" * 40,
    "openpine-contracts": "a" * 40,
}


def make_deployment_identity() -> DeploymentAdmissionIdentity:
    return DeploymentAdmissionIdentity(
        stack_id="test-stack",
        stack_manifest_hash=STACK_HASH,
        wheel_identities=tuple(
            (name, "5.0.0rc4", "sha256:" + "a" * 64)
            for name in (
                "openpine-contracts",
                "marketdata-provider",
                "pinelib",
                "pine2ast",
                "ast2python",
                "backtest_engine",
                "optimizer",
                "openpine",
            )
        ),
        schema_hashes={
            schema_id: "sha256:" + "b" * 64
            for schema_id in (
                "openpine.execution_context.v1",
                "openpine.intent.v2",
                "openpine.worker.protocol.v2",
                "openpine.checkpoint.v1",
                "openpine.checkpoint.proof.v1",
            )
        },
        capabilities=frozenset(
            {
                "closed_bar",
                "deterministic_clock",
                "isolated_worker",
                "broker_projection",
                "intent_tape_v2",
            }
        ),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )


def make_sealed_artifact(
    compile_meta: dict | None = None,
    *,
    python_code: str = "generated-source",
    semantic_profile: str = "strict_5x",
    producer_commits: dict[str, str] | None = None,
) -> dict[str, object]:
    commits = dict(producer_commits or _FAKE_COMMITS)
    return {
        "compile_meta": dict(compile_meta or {}),
        "python_code": python_code,
        "generated_artifact": seal_content_hash(
            {
                "schema_id": "openpine.generated_artifact.v2",
                "schema_version": "2.0.0",
                "producer": "ast2python",
                "producer_version": "5.0.0-rc.4",
                "producer_commit": commits["ast2python"],
                "stack_id": "openpine-5.0",
                "created_at_utc_ms": 1,
                "serializer_id": "openpine.canonical.json.v1",
                "content_hash_alg": "sha256",
                "source_hash": "sha256:" + "c" * 64,
                "frontend_artifact_hash": "sha256:" + "d" * 64,
                "ast_hash": "sha256:" + "e" * 64,
                "emitted_module_hash": _digest(
                    python_code, "openpine.generated_artifact.v2"
                ),
                "source_map_hash": "sha256:" + "f" * 64,
                "support_profile_hash": "sha256:" + "9" * 64,
                "lowering_version": "5.0.0rc4",
                "producer_commits": commits,
                "semantic_profile": semantic_profile,
                "required_runtime_capabilities": ["intent_tape_v2"],
                "import_allowlist": ["pinelib"],
                "entrypoint_module": "generated_strategy",
                "entrypoint_class": "GeneratedStrategy",
            },
            schema_id="openpine.generated_artifact.v2",
        ),
    }
