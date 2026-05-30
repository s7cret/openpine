from __future__ import annotations

import pytest

from openpine.artifacts.store import ArtifactStore
from openpine.compile.adapter import CompileResult
from openpine.compile.pipeline import compile_pipeline
from openpine.config import OpenPineConfig
from openpine.pine.source import PineSource


class Adapter:
    def __init__(self, result: CompileResult) -> None:
        self.result = result

    def compile(self, source_text: str, **kwargs) -> CompileResult:
        return self.result


def source() -> PineSource:
    return PineSource(id="pine_test", name="test", source_text='//@version=6\nstrategy("x")')


def test_failed_compile_does_not_write_generated_strategy(tmp_path, monkeypatch) -> None:
    config = OpenPineConfig(workspace_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr("openpine.artifacts.store.OpenPineConfig.load", lambda: config)

    result = compile_pipeline(
        source(),
        Adapter(CompileResult(success=False, errors=["compile failed"], compile_meta={})),
    )

    artifact_path = tmp_path / "data" / "artifacts" / "pine_test" / result["artifact_id"]
    assert result["success"] is False
    assert not (artifact_path / "generated_strategy.py").exists()
    assert (artifact_path / "compile_meta.json").exists()
    assert (artifact_path / "diagnostics.log").read_text() == "compile failed"


def test_artifact_store_removes_stale_generated_strategy_when_compile_has_no_code(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    artifact_path = store.save_artifact(
        artifact_id="art_test",
        source_id="pine_test",
        params_hash="default",
        python_code="class GeneratedStrategy: pass\n",
        compile_meta={"compile_status": "OK"},
    )
    assert (artifact_path / "generated_strategy.py").exists()

    store.save_artifact(
        artifact_id="art_test",
        source_id="pine_test",
        params_hash="default",
        python_code=None,
        compile_meta={"compile_status": "FAILED"},
    )

    assert not (artifact_path / "generated_strategy.py").exists()


def test_artifact_store_requires_compile_metadata(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    artifact_dir = tmp_path / "pine_test" / "art_missing_meta"
    artifact_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="Artifact metadata not found"):
        store.get_artifact("art_missing_meta", "pine_test")


def test_artifact_store_list_does_not_hide_corrupt_artifact_dirs(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    store.save_artifact(
        artifact_id="art_ok",
        source_id="pine_test",
        params_hash="default",
        python_code="class GeneratedStrategy: pass\n",
        compile_meta={"compile_status": "OK"},
    )
    (tmp_path / "pine_test" / "art_corrupt").mkdir()

    with pytest.raises(FileNotFoundError, match="Artifact metadata not found"):
        store.list_artifacts("pine_test")


def test_artifact_store_list_returns_artifacts_in_stable_order(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    store.save_artifact(
        artifact_id="art_b",
        source_id="pine_test",
        params_hash="default",
        python_code="class GeneratedStrategy: pass\n",
        compile_meta={"compile_status": "OK"},
    )
    store.save_artifact(
        artifact_id="art_a",
        source_id="pine_test",
        params_hash="default",
        python_code="class GeneratedStrategy: pass\n",
        compile_meta={"compile_status": "OK"},
    )

    assert [artifact["artifact_id"] for artifact in store.list_artifacts("pine_test")] == [
        "art_a",
        "art_b",
    ]


def test_successful_compile_requires_generated_python_code(tmp_path, monkeypatch) -> None:
    config = OpenPineConfig(workspace_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr("openpine.artifacts.store.OpenPineConfig.load", lambda: config)

    with pytest.raises(RuntimeError, match="did not include generated Python code"):
        compile_pipeline(source(), Adapter(CompileResult(success=True, python_code=None)))
