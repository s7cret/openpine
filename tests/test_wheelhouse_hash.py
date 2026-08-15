import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "hash_wheelhouse", ROOT / "scripts" / "hash_wheelhouse.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wheelhouse_hash_is_stable(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-0.0.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    mod = _mod()
    first = mod.file_sha256(wheel)
    assert first == mod.file_sha256(wheel)
    assert first.startswith("sha256:")
    rows = mod.collect_wheels(tmp_path)
    assert rows[wheel.name] == first
    payload = {
        "schema": "openpine.wheelhouse.v1",
        "not_a_release": True,
        "wheels": rows,
    }
    assert json.loads(json.dumps(payload))["not_a_release"] is True
