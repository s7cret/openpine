from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_candidate() -> dict:
    materializer = _load("materialize_stack_candidate.py")
    return materializer.materialize_candidate(
        {
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
                    "sha": "a" * 40,
                    "version": "5.0.0rc4",
                },
                "openpine": {
                    "repo": "s7cret/openpine",
                    "ref": "feat/5.0-isolated-worker",
                    "sha": "b" * 40,
                    "version": "5.0.0rc4",
                },
                "openpine-contracts": {
                    "repo": "s7cret/openpine-contracts",
                    "ref": "feat/5.0-contracts",
                    "sha": "c" * 40,
                    "version": "5.0.0rc4",
                },
            },
        },
        openpine_sha="b" * 40,
        created_at_utc="2026-08-20T21:00:00Z",
        provenance={"builder": "test", "run_id": "1"},
    )


def _wheel(
    directory: Path,
    *,
    name: str,
    version: str = "5.0.0rc4",
    requires: tuple[str, ...] = (),
    schemas: dict[str, bytes] | None = None,
) -> Path:
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    path = directory / filename
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {name}",
        f"Version: {version}",
    ]
    metadata.extend(f"Requires-Dist: {item}" for item in requires)
    metadata.append("")
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", "\n".join(metadata))
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\n")
        for schema_id, content in (schemas or {}).items():
            archive.writestr(
                f"openpine_contracts/schemas/{schema_id}.json",
                content,
            )
    return path


def _contracts_wheel(directory: Path) -> Path:
    return _wheel(
        directory,
        name="openpine-contracts",
        schemas={
            "openpine.run.v2": b'{"$id":"openpine.run.v2"}',
            "openpine.worker.protocol.v2": b'{"$id":"openpine.worker.protocol.v2"}',
        },
    )


def test_finalize_binds_exact_wheels_and_dependency_closure(tmp_path: Path) -> None:
    finalizer = _load("finalize_stack_candidate.py")
    resolver = _load("resolve_stack_candidate.py")
    _wheel(tmp_path, name="pinelib")
    _wheel(tmp_path, name="openpine", requires=("pinelib==5.0.0rc4",))
    _contracts_wheel(tmp_path)

    payload = finalizer.finalize_candidate(_source_candidate(), tmp_path)
    path = tmp_path / "stack-candidate-5.0.0-rc.4.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert payload["stage"] == "wheel-bound"
    assert payload["components"]["pinelib"]["wheel"]["filename"].endswith(".whl")
    assert payload["components"]["pinelib"]["wheel"]["sha256"].startswith("sha256:")
    assert set(payload["schema_hashes"]) == {
        "openpine.run.v2",
        "openpine.worker.protocol.v2",
    }
    assert all(value.startswith("sha256:") for value in payload["schema_hashes"].values())
    assert resolver.load_candidate(path) == payload


def test_finalize_rejects_vcs_stack_dependency(tmp_path: Path) -> None:
    finalizer = _load("finalize_stack_candidate.py")
    _wheel(tmp_path, name="pinelib")
    _contracts_wheel(tmp_path)
    _wheel(
        tmp_path,
        name="openpine",
        requires=("pinelib @ git+https://github.com/s7cret/pinelib.git@" + "a" * 40,),
    )

    with pytest.raises(finalizer.CandidateFinalizationError, match="VCS"):
        finalizer.finalize_candidate(_source_candidate(), tmp_path)


def test_finalize_rejects_stale_or_loose_stack_version(tmp_path: Path) -> None:
    finalizer = _load("finalize_stack_candidate.py")
    _wheel(tmp_path, name="pinelib")
    _contracts_wheel(tmp_path)
    _wheel(tmp_path, name="openpine", requires=("pinelib>=4.0.2",))

    with pytest.raises(finalizer.CandidateFinalizationError, match="exact dependency"):
        finalizer.finalize_candidate(_source_candidate(), tmp_path)


def test_finalize_rejects_missing_or_extra_stack_wheel(tmp_path: Path) -> None:
    finalizer = _load("finalize_stack_candidate.py")
    _wheel(tmp_path, name="pinelib")

    with pytest.raises(finalizer.CandidateFinalizationError, match="wheel set mismatch"):
        finalizer.finalize_candidate(_source_candidate(), tmp_path)
