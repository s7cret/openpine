from __future__ import annotations

from openpine.gateway.routes.pine_ops import _compile_native_rc6, _validate_native_rc6
from openpine.pine.source import PineSource


COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}


def test_gateway_native_compile_helper_emits_v3() -> None:
    source = PineSource(
        id="rc6-source",
        name="rc6_gateway",
        source_text='//@version=6\nindicator("gateway")\nplot(close)\n',
        source_path="rc6_gateway.pine",
    )

    result = _compile_native_rc6(source, producer_commits=COMMITS)

    assert result.success, result.errors
    assert result.generated_artifact is not None
    assert result.generated_artifact["schema_id"] == "openpine.generated_artifact.v3"
    assert result.consumer_bundle is not None


def test_gateway_native_validate_helper_verifies_consumer_bundle_only() -> None:
    source = PineSource(
        id="rc6-source",
        name="rc6_gateway",
        source_text='//@version=6\nindicator("gateway")\nplot(close)\n',
        source_path="rc6_gateway.pine",
    )

    bundle = _validate_native_rc6(source, producer_commit=COMMITS["pine2ast"])

    assert bundle["schema_id"] == "pine2ast.consumer_bundle.v1"
    assert bundle["producer"]["commit"] == COMMITS["pine2ast"]
