"""Actual eight-project build plus complete hashed hosted dependencies and sdists.

Invoke with files built/verified by the real execution_ci.prepare and completed
by the identical hash-locked download command. Its local procfs prerequisite
failure remains unaccepted. These are phase-local inputs, not runtime proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from zipfile import ZipFile

from packaging.utils import parse_sdist_filename, parse_wheel_filename
from packaging.version import Version
import pytest

from openpine.verification import qualification_hosted as hosted
from scripts.finalize_stack_candidate import finalize_candidate, normalize_name
from scripts.materialize_stack_candidate import materialize_candidate


@pytest.fixture
def real_inputs(candidate_inputs, tmp_path):
    bundle, report = candidate_inputs
    host = bundle.parent / "stack/openpine"
    source = materialize_candidate(json.loads((host / "candidates/stack-candidate-5.0.0-rc.6.template.json").read_bytes()),
        openpine_sha=report["host_commit"], created_at_utc="2026-10-07T00:00:00Z",
        provenance={"builder": "real-local-preflight", "run_id": "real-prepare"})
    mixed = tmp_path / "bundle/wheelhouse"
    mixed.mkdir(parents=True)
    for path in (bundle / "wheelhouse").iterdir():
        os.link(path, mixed / path.name)
    return host, source, mixed, tmp_path / "candidate-wheelhouse"


def test_complete_real_hosted_layout_versions_and_finalizer_input(real_inputs, candidate_inputs):
    host, source, mixed, selected = real_inputs
    expected_stack = {normalize_name(name): row["version"] for name, row in source["components"].items()}
    expected_dependencies = {}
    for name in ("ci-bootstrap-requirements.txt", "ci-runtime-requirements.txt"):
        for line in (host / "verification" / name).read_text().splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
            if match:
                expected_dependencies[normalize_name(match[1])] = match[2]
    wheels, sdists = {}, {}
    for path in mixed.iterdir():
        if path.suffix == ".whl":
            name, version, _, _ = parse_wheel_filename(path.name)
            assert name not in wheels
            wheels[name] = str(version)
        else:
            name, version = parse_sdist_filename(path.name)
            assert name not in sdists
            sdists[name] = str(version)
    assert wheels == {**expected_stack, **expected_dependencies}
    assert sdists == expected_stack
    setuptools = mixed / "setuptools-84.0.0-py3-none-any.whl"
    assert hashlib.sha256(setuptools.read_bytes()).hexdigest() == "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670"
    with ZipFile(setuptools) as archive:
        assert len([name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]) == 13
    exact = hosted.candidate_wheelhouse(source, mixed, selected)
    assert len(list(exact.iterdir())) == 8
    assert all(path.suffix == ".whl" for path in exact.iterdir())
    manifest = finalize_candidate(source, exact)
    assert manifest["stage"] == "wheel-bound" and len(manifest["components"]) == 8
    for name, row in manifest["components"].items():
        wheel = row["wheel"]
        path = exact / wheel["filename"]
        assert parse_wheel_filename(path.name)[1] == Version(row["version"])
        assert row["wheel"]["sha256"] == "sha256:" + hashlib.sha256((mixed / path.name).read_bytes()).hexdigest()
        assert path.stat().st_ino == (mixed / path.name).stat().st_ino
    _, report = candidate_inputs
    assert {name: row["sha"] for name, row in manifest["components"].items()} == {"openpine": report["host_commit"], **report["source_pins"]}


@pytest.mark.parametrize("mutation,code", [("missing", "missing-input"), ("missing-directory", "missing-input"),
    ("duplicate", "invalid-input"), ("filename-version", "invalid-input"),
    ("metadata-version", "invalid-input"), ("metadata-name", "invalid-input"), ("sdist-only", "missing-input")])
def test_real_hosted_layout_negative_inputs_fail_closed(real_inputs, mutation, code):
    host, source, mixed, selected = real_inputs
    wheel = mixed / "openpine-5.0.0rc6-py3-none-any.whl"
    if mutation in {"missing", "sdist-only"}:
        wheel.unlink()
    elif mutation == "missing-directory":
        shutil.rmtree(mixed)
    elif mutation == "duplicate":
        os.link(wheel, mixed / "openpine-5.0.0rc6-1-py3-none-any.whl")
    elif mutation == "filename-version":
        wheel.rename(mixed / "openpine-5.0.0rc5-py3-none-any.whl")
    else:
        replacement = wheel.with_suffix(".replacement")
        with ZipFile(wheel) as old, ZipFile(replacement, "w") as new:
            for item in old.infolist():
                content = old.read(item)
                if item.filename.endswith(".dist-info/METADATA"):
                    original = content
                    field, value = (b"Version", b"5.0.0rc5") if mutation == "metadata-version" else (b"Name", b"pinelib")
                    content = re.sub(rb"(?m)^" + field + rb": [^\r\n]+", field + b": " + value, content)
                    assert content != original
                new.writestr(item, content)
        replacement.replace(wheel)
    with pytest.raises(hosted.QualificationFailure) as caught:
        hosted.candidate_wheelhouse(source, mixed, selected)
    assert caught.value.code == code and not selected.exists()


def test_real_package_build_failure_uses_existing_closed_category(real_inputs, candidate_inputs, tmp_path):
    host, source, mixed, selected = real_inputs
    bundle, _ = candidate_inputs
    project = tmp_path / "broken-build"
    project.mkdir()
    # A real exported project with a deliberately absent build backend.
    shutil.copytree(host / "openpine", project / "openpine")
    metadata = (host / "pyproject.toml").read_text()
    (project / "pyproject.toml").write_text(metadata.replace('build-backend = "setuptools.build_meta"', 'build-backend = "absent_preflight_backend"'))
    executable = bundle.parent / "venv/bin/python"
    with pytest.raises(hosted.QualificationFailure) as caught:
        hosted.call([str(executable), "-I", "-B", "-m", "build", "--no-isolation", "--outdir", str(tmp_path / "failed-wheels"), str(project)],
                    tmp_path, tmp_path / "package-build-command")
    assert caught.value.code == "command-failed"
    assert not list((tmp_path / "failed-wheels").glob("*.whl"))
