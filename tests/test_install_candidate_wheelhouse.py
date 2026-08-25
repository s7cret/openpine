from __future__ import annotations

import hashlib
import importlib.util
from email.message import EmailMessage
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "install_candidate_wheelhouse",
        ROOT / "scripts" / "install_candidate_wheelhouse.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_wheel(
    directory: Path,
    *,
    name: str,
    version: str,
    requires: list[str],
    body: bytes = b"payload",
) -> Path:
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    path = directory / filename
    metadata = EmailMessage()
    metadata["Metadata-Version"] = "2.1"
    metadata["Name"] = name
    metadata["Version"] = version
    for item in requires:
        metadata["Requires-Dist"] = item
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA", str(metadata))
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        archive.writestr(f"{name.replace('-', '_')}/__init__.py", body.decode("latin1"))
    return path


def test_hash_mismatch_is_fail_closed(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-0.0.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    mod = _mod()
    with pytest.raises(mod.WheelhouseInstallError, match="hash mismatch"):
        mod.verify_local_hashes(
            tmp_path,
            {wheel.name: "sha256:" + "0" * 64},
        )


def test_missing_hash_is_fail_closed(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-0.0.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    mod = _mod()
    with pytest.raises(mod.WheelhouseInstallError, match="wheel set mismatch"):
        mod.verify_local_hashes(tmp_path, {})


def test_missing_expected_wheel_file_is_fail_closed(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-0.0.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    mod = _mod()
    with pytest.raises(mod.WheelhouseInstallError, match="wheel set mismatch"):
        mod.verify_local_hashes(
            tmp_path,
            {
                wheel.name: _sha256(wheel.read_bytes()),
                "missing-0.0.1-py3-none-any.whl": _sha256(b"missing"),
            },
        )


def test_only_wheel_bound_candidate_can_supply_install_hashes() -> None:
    mod = _mod()
    source = {"stage": "source", "components": {}}
    with pytest.raises(mod.WheelhouseInstallError, match="wheel-bound"):
        mod.wheel_hashes_from_candidate(source)

    bound = {
        "stage": "wheel-bound",
        "components": {
            "pinelib": {
                "wheel": {
                    "filename": "pinelib-5.0.0rc5-py3-none-any.whl",
                    "sha256": "sha256:" + "a" * 64,
                }
            }
        },
    }
    assert mod.wheel_hashes_from_candidate(bound) == {
        "pinelib-5.0.0rc5-py3-none-any.whl": "sha256:" + "a" * 64
    }


def test_third_party_requirements_skip_stack_and_vcs_pins(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path,
        name="marketdata-provider",
        version="4.0.2",
        requires=[
            "httpx>=0.25",
            "pinelib @ git+https://github.com/s7cret/pinelib.git@abc",
            "openpine-contracts>=1.0.0rc1",
            'pyarrow>=14; extra == "parquet"',
        ],
    )
    rows = _mod().third_party_requirements(wheel)
    assert rows == ["httpx>=0.25"]


def test_hashed_requirements_cover_local_wheels_and_deps(tmp_path: Path) -> None:
    wheel = tmp_path / "demo-0.0.1-py3-none-any.whl"
    payload = b"wheel-bytes"
    wheel.write_bytes(payload)
    dep = tmp_path / "deps"
    dep.mkdir()
    httpx = dep / "httpx-0.28.1-py3-none-any.whl"
    httpx.write_bytes(b"httpx-bytes")
    text = _mod().render_hashed_requirements(
        [
            (wheel, _sha256(payload)),
            (httpx, _sha256(b"httpx-bytes")),
        ]
    )
    assert "--hash=sha256:" in text
    assert "demo==0.0.1" in text
    assert "httpx==0.28.1" in text
    assert "@ git+" not in text


def test_install_argv_requires_hashes_and_keeps_no_deps(tmp_path: Path) -> None:
    reqs = tmp_path / "hashed-requirements.txt"
    reqs.write_text("demo==0.0.1 --hash=sha256:ab\n", encoding="utf-8")
    mod = _mod()
    argv = mod.install_argv(reqs, python="python3", find_links=tmp_path)
    assert argv[:4] == ["python3", "-m", "pip", "install"]
    assert "--require-hashes" in argv
    assert "--no-deps" in argv
    assert "--find-links" in argv
    assert "--no-index" in argv
    with pytest.raises(mod.WheelhouseInstallError, match="no-deps"):
        mod.install_argv(reqs, python="python3", allow_resolver=True)


def test_download_argv_is_wheels_only(tmp_path: Path) -> None:
    argv = _mod().download_argv(["httpx>=0.25"], tmp_path, python="python3")
    assert argv[:4] == ["python3", "-m", "pip", "download"]
    assert "--only-binary" in argv
    assert ":all:" in argv
    assert "httpx>=0.25" in argv
    assert str(tmp_path) in argv
