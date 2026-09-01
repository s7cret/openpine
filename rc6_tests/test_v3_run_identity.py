from __future__ import annotations

from copy import deepcopy

import pytest

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import generated_artifact_hash, verified_generated_source
from openpine_contracts import AdmitError


SOURCE = '//@version=6\nindicator("rc6-identity")\nplot(close)\n'
COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}


def _artifact_record() -> dict[str, object]:
    result = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="generated_rc6_identity",
        source_name="rc6-identity.pine",
        producer_commits=COMMITS,
    )
    assert result.success, result.errors
    return {
        "generated_artifact": result.generated_artifact,
        "python_code": result.python_code,
        "consumer_bundle": result.consumer_bundle,
        "source_map": result.source_map,
        "compile_meta": result.compile_meta,
    }


def test_run_identity_accepts_native_v3_artifact_and_exact_module_bytes() -> None:
    artifact = _artifact_record()
    envelope = artifact["generated_artifact"]
    assert isinstance(envelope, dict)

    assert generated_artifact_hash(artifact) == envelope["content_hash"]
    assert verified_generated_source(artifact) == str(artifact["python_code"]).encode("utf-8")


def test_run_identity_rejects_tampered_v3_module_bytes() -> None:
    artifact = deepcopy(_artifact_record())
    artifact["python_code"] = str(artifact["python_code"]) + "\n# tampered\n"

    with pytest.raises(AdmitError) as error_info:
        verified_generated_source(artifact)

    assert error_info.value.code == "GENERATED_ARTIFACT_HASH_MISMATCH"
