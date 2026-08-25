from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_immutable_identity_env.py"
SCHEMA = "openpine.stack-candidate.v2"
SHA = {
    "openpine": "a" * 40,
    "pine2ast": "b" * 40,
    "ast2python": "c" * 40,
    "pinelib": "d" * 40,
    "openpine-contracts": "e" * 40,
}


def _seal(payload: dict[str, object]) -> None:
    encoded = json.dumps(
        {"domain": SCHEMA, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload["manifest_hash"] = "sha256:" + hashlib.sha256(encoded).hexdigest()


def _component(name: str, sha: str) -> dict[str, object]:
    return {
        "sha": sha,
        "version": "5.0.0rc5",
        "wheel": {
            "filename": f"{name}-5.0.0rc5-py3-none-any.whl",
            "sha256": "sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest(),
        },
    }


def _candidate(
    tmp_path: Path,
    *,
    components: dict[str, dict[str, object]] | None = None,
    stage: str = "wheel-bound",
) -> Path:
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "stage": stage,
        "components": components or {name: _component(name, sha) for name, sha in SHA.items()},
    }
    _seal(payload)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(candidate: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--candidate", str(candidate), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_renderer_emits_all_exact_non_secret_producer_identities(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    output = tmp_path / "immutable-identity.env"

    result = _run(candidate, output)

    assert result.returncode == 0, result.stderr
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines == [
        f"OPENPINE_BUILD_COMMIT={SHA['openpine']}",
        f"OPENPINE_DEPLOYMENT_COMMIT={SHA['openpine']}",
        f"OPENPINE_PRODUCER_COMMIT={SHA['pine2ast']}",
        "OPENPINE_PRODUCER_COMMITS_JSON="
        + json.dumps(
            {
                "pine2ast": SHA["pine2ast"],
                "ast2python": SHA["ast2python"],
                "pinelib": SHA["pinelib"],
                "openpine-contracts": SHA["openpine-contracts"],
            },
            separators=(",", ":"),
        ),
    ]
    assert "TOKEN" not in output.read_text(encoding="utf-8")
    assert output.stat().st_mode & 0o777 == 0o600


def test_renderer_fails_closed_when_required_component_is_missing(tmp_path: Path) -> None:
    components = {name: _component(name, sha) for name, sha in SHA.items() if name != "pine2ast"}
    candidate = _candidate(tmp_path, components=components)
    output = tmp_path / "immutable-identity.env"

    result = _run(candidate, output)

    assert result.returncode != 0
    assert "required component missing: pine2ast" in result.stderr
    assert not output.exists()


def test_renderer_rejects_source_stage_candidate(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, stage="source")
    output = tmp_path / "immutable-identity.env"

    result = _run(candidate, output)

    assert result.returncode != 0
    assert "wheel-bound candidate stage required" in result.stderr
    assert not output.exists()


def test_renderer_rejects_manifest_hash_drift(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["components"]["pine2ast"]["sha"] = "f" * 40
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "immutable-identity.env"

    result = _run(candidate, output)

    assert result.returncode != 0
    assert "candidate manifest_hash mismatch" in result.stderr
    assert not output.exists()


def test_renderer_rejects_wheel_bound_candidate_without_wheel_identity(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    del payload["components"]["pine2ast"]["wheel"]
    payload.pop("manifest_hash")
    _seal(payload)
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "immutable-identity.env"

    result = _run(candidate, output)

    assert result.returncode != 0
    assert "component wheel identity missing: pine2ast" in result.stderr
    assert not output.exists()


def test_renderer_atomically_replaces_output_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    target = tmp_path / "target.env"
    target.write_text("sentinel\n", encoding="utf-8")
    output = tmp_path / "immutable-identity.env"
    output.symlink_to(target)

    result = _run(candidate, output)

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "sentinel\n"
    assert not output.is_symlink()
    assert output.read_text(encoding="utf-8").startswith("OPENPINE_BUILD_COMMIT=")
