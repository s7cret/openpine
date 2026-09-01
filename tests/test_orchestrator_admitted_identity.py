from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openpine.data.orchestrator import StorageUnavailableError, _default_candle_store
from openpine.runtime.admitted_manifest import AdmittedManifestError


def _write_manifest(path, *, sha: str | None, manifest_hash: str = "sha256:" + "d" * 64) -> None:
    components = {}
    if sha is not None:
        components["marketdata-provider"] = {"sha": sha, "version": "5.0.0rc6"}
    payload = {
        "schema": "openpine.stack-candidate.v2",
        "stage": "wheel-bound",
        "id": "5.0.0-rc.6",
        "not_a_release": True,
        "manifest_hash": manifest_hash,
        "components": components,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_default_candle_store_fail_closed_without_admitted_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENPINE_CANDIDATE_MANIFEST", raising=False)
    monkeypatch.delenv("OPENPINE_DEPLOYMENT_MANIFEST", raising=False)
    monkeypatch.setattr(
        "openpine.data.orchestrator.DEFAULT_CONFIG",
        SimpleNamespace(data_cache_root=tmp_path, data_dir=tmp_path),
    )
    with pytest.raises((AdmittedManifestError, StorageUnavailableError)):
        _default_candle_store()


def test_default_candle_store_binds_admitted_marketdata_identity(monkeypatch, tmp_path) -> None:
    manifest = tmp_path / "candidate.json"
    sha = "e" * 40
    stack = "sha256:" + "d" * 64
    _write_manifest(manifest, sha=sha, manifest_hash=stack)
    monkeypatch.setenv("OPENPINE_CANDIDATE_MANIFEST", str(manifest))
    monkeypatch.setenv("OPENPINE_DEPLOYMENT_MANIFEST", str(manifest))
    monkeypatch.setattr(
        "openpine.data.orchestrator.DEFAULT_CONFIG",
        SimpleNamespace(data_cache_root=tmp_path, data_dir=tmp_path),
    )
    store = _default_candle_store()
    assert store.producer_commit == sha
    assert store.stack_id == stack


def test_default_candle_store_fail_closed_without_marketdata_sha(monkeypatch, tmp_path) -> None:
    manifest = tmp_path / "candidate.json"
    _write_manifest(manifest, sha="0" * 40)
    monkeypatch.setenv("OPENPINE_CANDIDATE_MANIFEST", str(manifest))
    monkeypatch.setenv("OPENPINE_DEPLOYMENT_MANIFEST", str(manifest))
    monkeypatch.setattr(
        "openpine.data.orchestrator.DEFAULT_CONFIG",
        SimpleNamespace(data_cache_root=tmp_path, data_dir=tmp_path),
    )
    with pytest.raises(StorageUnavailableError, match="producer_commit"):
        _default_candle_store()
