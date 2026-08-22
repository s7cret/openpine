from __future__ import annotations

from ast2python.artifact import _digest
from openpine.admission import DeploymentAdmissionIdentity
from openpine_contracts import seal_content_hash

STACK_HASH = "sha256:" + "e" * 64
_FAKE_COMMITS = {
    "pine2ast": "1" * 40,
    "ast2python": "2" * 40,
    "pinelib": "3" * 40,
    "openpine-contracts": "4" * 40,
}


def make_deployment_identity() -> DeploymentAdmissionIdentity:
    return DeploymentAdmissionIdentity(
        stack_id="test-stack",
        stack_manifest_hash=STACK_HASH,
        wheel_identities=(("openpine", "5.0.0rc3", "sha256:" + "a" * 64),),
        schema_hashes={"openpine.run.v2": "sha256:" + "b" * 64},
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
) -> dict[str, object]:
    return {
        "compile_meta": dict(compile_meta or {}),
        "python_code": python_code,
        "generated_artifact": seal_content_hash(
            {
                "schema_id": "openpine.generated_artifact.v2",
                "schema_version": "2.0.0",
                "producer": "ast2python",
                "producer_version": "5.0.0-rc.3",
                "producer_commit": _FAKE_COMMITS["ast2python"],
                "stack_id": "test-stack",
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
                "lowering_version": "5.0.0rc3",
                "producer_commits": dict(_FAKE_COMMITS),
                "semantic_profile": "strict_5x",
                "required_runtime_capabilities": ["intent_tape_v2"],
                "import_allowlist": ["pinelib"],
                "entrypoint_module": "generated_strategy",
                "entrypoint_class": "GeneratedStrategy",
            },
            schema_id="openpine.generated_artifact.v2",
        ),
    }
