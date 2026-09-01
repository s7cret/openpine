from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template() -> dict[str, object]:
    return {
        "schema": "openpine.stack-candidate-template.v1",
        "id": "5.0.0-rc.4",
        "not_a_release": True,
        "admission": {
            "capabilities": ["closed_bar", "deterministic_clock"],
            "semantic_profiles": ["strict_5x"],
            "finality_policies": ["CLOSED_BAR_ONLY"],
            "warmup_policies": ["CALC_ONLY"],
            "score_policies": ["ALL_BARS"],
        },
        "components": {
            "pinelib": {
                "repo": "s7cret/pinelib",
                "ref": "feat/5.0-intent-tape",
                "sha": "b" * 40,
                "version": "5.0.0rc4",
            },
            "openpine": {
                "repo": "s7cret/openpine",
                "ref": "feat/5.0-isolated-worker",
                "sha": SHA,
                "version": "5.0.0rc4",
            },
        },
    }


def test_materialized_candidate_binds_openpine_sha_and_manifest_hash(tmp_path: Path) -> None:
    materializer = _load_script("materialize_stack_candidate.py")
    resolver = _load_script("resolve_stack_candidate.py")

    payload = materializer.materialize_candidate(
        _template(),
        openpine_sha=SHA,
        created_at_utc="2026-08-20T21:00:00Z",
        provenance={"builder": "github-actions", "run_id": "123"},
    )
    path = tmp_path / "stack-candidate-5.0.0-rc.4.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert payload["schema"] == "openpine.stack-candidate.v2"
    assert payload["stage"] == "source"
    assert payload["components"]["openpine"]["sha"] == SHA
    assert payload["components"]["openpine"]["version"] == "5.0.0rc4"
    assert payload["admission"]["capabilities"] == [
        "closed_bar",
        "deterministic_clock",
    ]
    assert payload["manifest_hash"].startswith("sha256:")
    assert resolver.load_candidate(path) == payload


def test_materialized_candidate_hash_detects_tampering(tmp_path: Path) -> None:
    materializer = _load_script("materialize_stack_candidate.py")
    resolver = _load_script("resolve_stack_candidate.py")
    payload = materializer.materialize_candidate(
        _template(),
        openpine_sha=SHA,
        created_at_utc="2026-08-20T21:00:00Z",
        provenance={"builder": "github-actions", "run_id": "123"},
    )
    payload["components"]["pinelib"]["sha"] = "c" * 40
    path = tmp_path / "stack-candidate-5.0.0-rc.4.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(resolver.CandidateSelectionError, match="manifest_hash"):
        resolver.load_candidate(path)


def test_materializer_rejects_placeholder_or_dirty_identity() -> None:
    materializer = _load_script("materialize_stack_candidate.py")

    with pytest.raises(materializer.CandidateMaterializationError, match="40 lowercase hex"):
        materializer.materialize_candidate(
            _template(),
            openpine_sha="THIS_CHECKOUT",
            created_at_utc="2026-08-20T21:00:00Z",
            provenance={"builder": "github-actions", "run_id": "123"},
        )


def test_template_is_not_discovered_as_an_active_candidate(tmp_path: Path) -> None:
    resolver = _load_script("resolve_stack_candidate.py")
    template_dir = tmp_path / "candidates"
    template_dir.mkdir()
    (template_dir / "stack-candidate-5.0.0-rc.4.template.json").write_text(
        json.dumps(_template()), encoding="utf-8"
    )

    assert resolver.resolve_candidate(tmp_path) is None
