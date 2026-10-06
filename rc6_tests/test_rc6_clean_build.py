"""Real build/lib contamination regression and exact installed-tree guard."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = (
    "_compat/__init__.py",
    "_compat/parquet.py",
    "_compat/structlog.py",
    "compile/adapter.py",
    "data/direct_data_provider.py",
)


def load_owner(path: Path):
    spec = importlib.util.spec_from_file_location("clean_build_test_owner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def owner():
    return load_owner(ROOT / "scripts/rc6_stabilization/clean_build.py")


@pytest.fixture
def checkout(tmp_path):
    source = tmp_path / "checkout"
    source.mkdir()
    git = shutil.which("git")
    assert git

    def run_git(*args):
        return subprocess.check_output([git, "-C", str(source), *args], text=True).strip()  # noqa: S603

    run_git("init", "-q")
    run_git("config", "user.email", "ci@example.invalid")
    run_git("config", "user.name", "CI fixture")
    (source / ".gitignore").write_text("build/\ndist/\n*.egg-info/\n")
    (source / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools>=77','wheel']\n"
        "build-backend='setuptools.build_meta'\n"
        "[project]\nname='openpine'\nversion='0.0.1'\n"
        "requires-python='>=3.13,<3.14'\n"
        "[tool.setuptools.packages.find]\ninclude=['openpine*']\n"
        "[tool.setuptools.package-data]\nopenpine=['py.typed','resource.json']\n"
    )
    package = source / "openpine"
    package.mkdir()
    (package / "__init__.py").write_text("answer = 42\n")
    (package / "resource.json").write_text('{"required":true}\n')
    (package / "py.typed").touch()
    run_git("add", ".")
    run_git("commit", "-qm", "candidate source")
    for relative in EXTRAS:
        path = source / "build/lib/openpine" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("obsolete = True\n")
    return source


def run_build(source, output, kind, log):
    output.mkdir(parents=True)
    with log.open("wb") as stream:
        result = subprocess.run(  # noqa: S603 -- actual pinned test interpreter, no shell
            [sys.executable, "-I", "-m", "build", "--no-isolation", kind, "--outdir", str(output), str(source)],
            stdout=stream, stderr=subprocess.STDOUT, check=False,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", SOURCE_DATE_EPOCH="1700000000"),
        )
    assert result.returncode == 0, log.read_text()[-4000:]


def test_stale_build_lib_is_preserved_but_never_shipped_normal_or_rebuilt(checkout, tmp_path, owner):
    before = {relative: (checkout / "build/lib/openpine" / relative).read_bytes() for relative in EXTRAS}
    dirty_wheels = tmp_path / "dirty-wheels"
    run_build(checkout, dirty_wheels, "--wheel", tmp_path / "original-build.log")
    contaminated = next(dirty_wheels.glob("*.whl"))
    with zipfile.ZipFile(contaminated) as archive:
        assert all("openpine/" + relative in archive.namelist() for relative in EXTRAS)
    with pytest.raises(ValueError, match="wheel package tree mismatch.*extra="):
        owner.verify_wheel_tree(checkout, contaminated)

    builder = load_owner(ROOT / "scripts/build_candidate_wheelhouse.py")
    clean_wheels = tmp_path / "clean-wheels"
    report = builder.build_wheel(checkout, clean_wheels, no_isolation=True)
    normal = next(clean_wheels.glob("*.whl"))
    assert report["package_tree"] == owner.source_package_tree(checkout)
    assert owner.verify_wheel_tree(checkout, normal) == report["package_tree"]
    attempt = next((clean_wheels / "build-attempts").glob("*/build"))
    assert not any((attempt / "source/build/lib/openpine" / relative).exists() for relative in EXTRAS)
    assert json.loads((attempt / "source.json").read_text())["package_tree"] == report["package_tree"]

    sdists = tmp_path / "sdists"
    run_build(attempt / "source", sdists, "--sdist", tmp_path / "sdist-build.log")
    unpacked = tmp_path / "sdist-source"
    unpacked.mkdir()
    with tarfile.open(next(sdists.glob("*.tar.gz"))) as archive:
        archive.extractall(unpacked, filter="data")
    rebuilt_wheels = tmp_path / "rebuilt-wheels"
    run_build(next(unpacked.iterdir()), rebuilt_wheels, "--wheel", tmp_path / "rebuilt-build.log")
    assert owner.verify_wheel_tree(checkout, next(rebuilt_wheels.glob("*.whl"))) == report["package_tree"]
    assert {relative: (checkout / "build/lib/openpine" / relative).read_bytes() for relative in EXTRAS} == before
    assert contaminated.exists()


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed"])
def test_wheel_source_identity_rejects_each_drift(owner, checkout, tmp_path, mutation):
    wheel = tmp_path / "drift.whl"
    files = {"__init__.py": b"answer = 42\n", "resource.json": b'{"required":true}\n', "py.typed": b""}
    if mutation == "missing":
        files.pop("resource.json")
    elif mutation == "extra":
        files["compile/adapter.py"] = b"obsolete = True\n"
    else:
        files["resource.json"] = b'{"required":false}\n'
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr("openpine/" + name, data)
    with pytest.raises(ValueError, match="wheel package tree mismatch"):
        owner.verify_wheel_tree(checkout, wheel)


@pytest.mark.parametrize("mutation", ["dirty", "inside-source", "symlink"])
def test_clean_source_staging_rejects_unsafe_or_uncommitted_inputs(owner, checkout, tmp_path, mutation):
    attempt = tmp_path / "attempt"
    if mutation == "dirty":
        (checkout / "openpine/__init__.py").write_text("answer = 0\n")
    elif mutation == "inside-source":
        attempt = checkout / "build/new-attempt"
    else:
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        attempt = link / "new-attempt"
    with pytest.raises(ValueError):
        owner.stage_git_source(checkout, attempt)
    assert not attempt.exists()


def test_installed_guard_detects_extra_files_absent_from_record(owner, checkout, tmp_path, monkeypatch):
    from types import SimpleNamespace
    prefix = tmp_path / "venv"
    package = prefix / "lib/site-packages/openpine"
    package.parent.mkdir(parents=True)
    shutil.copytree(checkout / "openpine", package)
    expected = {"openpine": {"package_tree": owner.source_package_tree(checkout)}}
    monkeypatch.setattr(owner.sys, "prefix", str(prefix))
    monkeypatch.setattr(owner.sys, "flags", SimpleNamespace(isolated=True))
    monkeypatch.setattr(owner.importlib, "import_module", lambda name: SimpleNamespace(__file__=str(package / "__init__.py")))
    assert owner.check_installed(expected)["components"]["openpine"]["tree_sha256"] == expected["openpine"]["package_tree"]["tree_sha256"]
    (package / "forgotten.py").write_text("obsolete = True\n")
    with pytest.raises(ValueError, match="extra=.*forgotten.py"):
        owner.check_installed(expected)


@pytest.mark.parametrize('kind', ['wheel', 'sdist', 'installed'])
@pytest.mark.parametrize('mutation', ['stale-five', 'missing-resource', 'changed-resource'])
def test_acceptance_replay_requires_exact_frozen_runtime_and_resource_tree(kind, mutation):
    import hashlib
    from openpine.verification.stabilization_evidence import verify_package_source_inventory
    members = {'openpine/__init__.py': b'answer = 42\n', 'openpine/resource.json': b'{"required":true}\n'}
    source = {name: {'sha256': 'sha256:' + hashlib.sha256(data).hexdigest()} for name, data in members.items()}
    verify_package_source_inventory(source, members, 'openpine', kind=kind)
    if mutation == 'stale-five':
        members.update({'openpine/' + relative: b'obsolete = True\n' for relative in EXTRAS})
    elif mutation == 'missing-resource':
        members.pop('openpine/resource.json')
    else:
        members['openpine/resource.json'] = b'{"required":false}\n'
    with pytest.raises(ValueError, match=kind + ' package source inventory differs from frozen candidate'):
        verify_package_source_inventory(source, members, 'openpine', kind=kind)
