from __future__ import annotations

from copy import deepcopy

import pytest

from openpine.artifacts.store import ArtifactStore
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter


SOURCE = '//@version=6\nindicator("rc6-store")\nplot(close)\n'
COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}


def _compiled():
    result = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="generated_rc6_store",
        source_name="rc6-store.pine",
        producer_commits=COMMITS,
    )
    assert result.success, result.errors
    return result


def test_artifact_store_round_trips_native_v3_lineage(tmp_path) -> None:
    result = _compiled()
    assert result.generated_artifact is not None
    assert result.python_code is not None
    assert result.ast_json is not None
    assert result.source_map is not None
    assert result.consumer_bundle is not None
    store = ArtifactStore(root=tmp_path)
    artifact_id = store.artifact_id_for_envelope(result.generated_artifact)

    path = store.save_artifact(
        artifact_id=artifact_id,
        source_id="src_rc6",
        params_hash="default",
        python_code=result.python_code,
        compile_meta=result.compile_meta,
        source_text=SOURCE,
        ast_json=result.ast_json,
        source_map=result.source_map,
        generated_artifact=result.generated_artifact,
        consumer_bundle=result.consumer_bundle,
    )

    assert path.joinpath("consumer_bundle.json").exists()
    loaded = store.get_artifact(artifact_id, "src_rc6")
    assert loaded["generated_artifact"] == result.generated_artifact
    assert loaded["consumer_bundle"] == result.consumer_bundle
    assert loaded["source_map"] == result.source_map
    assert loaded["python_code"] == result.python_code


def test_artifact_store_rejects_v3_emitted_module_tampering(tmp_path) -> None:
    result = _compiled()
    assert result.generated_artifact is not None
    assert result.ast_json is not None
    assert result.source_map is not None
    assert result.consumer_bundle is not None
    store = ArtifactStore(root=tmp_path)
    artifact_id = store.artifact_id_for_envelope(result.generated_artifact)

    with pytest.raises(ValueError, match="emitted module hash"):
        store.save_artifact(
            artifact_id=artifact_id,
            source_id="src_rc6",
            params_hash="default",
            python_code=(result.python_code or "") + "\n# tampered\n",
            compile_meta=result.compile_meta,
            source_text=SOURCE,
            ast_json=result.ast_json,
            source_map=deepcopy(result.source_map),
            generated_artifact=deepcopy(result.generated_artifact),
            consumer_bundle=deepcopy(result.consumer_bundle),
        )
