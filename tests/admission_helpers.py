from __future__ import annotations

from openpine.admission import DeploymentAdmissionIdentity
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter

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
            (name, "5.0.0rc6", "sha256:" + "a" * 64)
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
    del python_code
    source_text = "//@version=6\nstrategy(\"fixture\")\n"
    compiled = NativeRC6CompilerAdapter().compile(
        source_text,
        producer_commits=dict(producer_commits or _FAKE_COMMITS),
    )
    if not compiled.success or compiled.generated_artifact is None:
        raise AssertionError(compiled.diagnostics)
    return {
        "compile_meta": {
            **compiled.compile_meta,
            **dict(compile_meta or {}),
            "semantic_profile": semantic_profile,
        },
        "python_code": compiled.python_code,
        "generated_class": compiled.python_code.encode("utf-8"),
        "source_text": source_text,
        "ast_json": compiled.ast_json,
        "source_map": compiled.source_map,
        "generated_artifact": compiled.generated_artifact,
        "consumer_bundle": compiled.consumer_bundle,
        "frontend_artifact": compiled.frontend_artifact,
        "support_profile": compiled.support_profile,
        "ast_artifact": compiled.ast_artifact,
    }
