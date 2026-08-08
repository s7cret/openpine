from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

from openpine.stack_lock import (
    EXPECTED_COMPONENTS,
    _github_repository_from_url,
    _transitive_pin_errors,
    load_stack_lock,
    package_tree_identity,
    stack_lock_identity,
    stack_lock_summary,
    validate_stack_lock,
)


def test_github_repository_origin_accepts_checkout_urls_with_or_without_git_suffix() -> None:
    assert (
        _github_repository_from_url("https://github.com/s7cret/optimizer")
        == "s7cret/optimizer"
    )
    assert (
        _github_repository_from_url("git@github.com:s7cret/optimizer.git")
        == "s7cret/optimizer"
    )


def test_github_repository_origin_rejects_lookalikes() -> None:
    rejected = (
        "https://evil.example/github.com/s7cret/optimizer.git",
        "https://github.com.evil.example/s7cret/optimizer.git",
        "file://github.com/s7cret/optimizer.git",
        "ftp://github.com/s7cret/optimizer.git",
        "https://github.com:444/s7cret/optimizer.git",
    )
    assert all(_github_repository_from_url(origin) is None for origin in rejected)


def test_transitive_pin_validation_rejects_stale_stack_dependency_refs() -> None:
    expected = {
        "s7cret/pinelib": "a" * 40,
        "s7cret/backtest_engine": "b" * 40,
    }
    project = {
        "dependencies": [
            "pinelib @ git+https://github.com/s7cret/pinelib.git@" + "1" * 40,
        ],
        "optional-dependencies": {
            "dev": [
                "backtest-engine @ git+https://github.com/s7cret/backtest_engine.git@"
                + "b" * 40,
            ]
        },
    }

    errors = _transitive_pin_errors("ast2python", project, expected)

    assert errors == (
        "stack lock component ast2python dependency s7cret/pinelib ref does not match lock",
    )

    project["dependencies"] = [
        "pinelib @ git+https://github.com/s7cret/pinelib.git@" + "a" * 40,
    ]
    assert _transitive_pin_errors("ast2python", project, expected) == ()


def test_packaged_stack_lock_is_complete_and_immutable() -> None:
    lock = load_stack_lock()
    assert lock["schema"] == "openpine.stack-lock.v1"
    assert lock["release"] == "4.0.1"
    assert [item["name"] for item in lock["components"]] == list(EXPECTED_COMPONENTS)
    assert all(item["version"] == "4.0.1" for item in lock["components"])
    siblings = lock["components"][1:]
    assert all(re.fullmatch(r"(?!0{40})[0-9a-f]{40}", item["commit"]) for item in siblings)
    self_identity = lock["components"][0]
    assert "commit" not in self_identity
    assert re.fullmatch(r"(?!0{64})[0-9a-f]{64}", self_identity["tree_sha256"])
    root = Path(__file__).resolve().parents[1]
    assert self_identity["tree_sha256"] == package_tree_identity(root / "openpine")
    assert validate_stack_lock(lock) == ()


def test_stack_lock_identity_is_canonical_and_tamper_evident() -> None:
    lock = load_stack_lock()
    identity = stack_lock_identity(lock)
    assert re.fullmatch(r"[0-9a-f]{64}", identity)
    reordered = {key: lock[key] for key in reversed(lock)}
    assert stack_lock_identity(reordered) == identity
    tampered = copy.deepcopy(lock)
    tampered["components"][1]["commit"] = "1" * 40
    assert stack_lock_identity(tampered) != identity


def test_stack_lock_validation_reports_component_and_sha_errors() -> None:
    lock = load_stack_lock()
    broken = copy.deepcopy(lock)
    broken["components"] = broken["components"][:-1]
    broken["components"][1]["commit"] = "main"
    errors = validate_stack_lock(broken)
    assert any("components" in error for error in errors)
    assert any("immutable" in error for error in errors)


def test_stack_lock_validation_rejects_valid_looking_wrong_package_and_repository() -> None:
    lock = load_stack_lock()
    broken = copy.deepcopy(lock)
    broken["components"][1]["package"] = "lookalike-package"
    broken["components"][2]["repository"] = "attacker/ast2python"

    errors = validate_stack_lock(broken)

    assert any("pine2ast package" in error for error in errors)
    assert any("ast2python repository" in error for error in errors)


def test_stack_lock_validation_checks_dependency_and_ci_refs(tmp_path: Path) -> None:
    lock = load_stack_lock()
    root = tmp_path / "openpine"
    (root / ".github" / "workflows").mkdir(parents=True)
    dependencies = []
    workflow_lines = []
    for item in lock["components"][1:]:
        dependencies.append(
            f'    "{item["package"]} @ git+https://github.com/{item["repository"]}.git@{item["commit"]}",'
        )
        workflow_lines.extend(
            [
                f"          repository: {item['repository']}",
                f"          ref: {item['commit']}",
            ]
        )
    (root / "pyproject.toml").write_text(
        "[project]\nname='openpine'\nversion='4.0.1'\ndependencies=[\n"
        + "\n".join(dependencies)
        + "\n]\n",
        encoding="utf-8",
    )
    workflow_text = "\n".join(workflow_lines) + "\n"
    stack_workflow = root / ".github" / "workflows" / "stack-ci.yml"
    stack_workflow.write_text(workflow_text, encoding="utf-8")
    backend_workflow = root / ".github" / "workflows" / "ci.yml"
    backend_workflow.write_text(
        "PINE_STACK_ROOT: ${{ github.workspace }}/stack\n" + workflow_text,
        encoding="utf-8",
    )

    assert validate_stack_lock(lock, root=root) == ()

    stack_workflow.write_text(
        workflow_text.replace(lock["components"][1]["commit"], "1" * 40),
        encoding="utf-8",
    )
    errors = validate_stack_lock(lock, root=root)
    assert any("stack-ci ref" in error and "pine2ast" in error for error in errors)

    stack_workflow.write_text(workflow_text, encoding="utf-8")
    backend_workflow.write_text(
        (
            "PINE_STACK_ROOT: ${{ github.workspace }}/stack\n" + workflow_text
        ).replace(lock["components"][1]["commit"], "1" * 40),
        encoding="utf-8",
    )
    errors = validate_stack_lock(lock, root=root)
    assert any("backend-ci ref" in error and "pine2ast" in error for error in errors)

    backend_workflow.write_text(workflow_text, encoding="utf-8")
    errors = validate_stack_lock(lock, root=root)
    assert any("PINE_STACK_ROOT" in error for error in errors)


def test_stack_lock_rejects_zero_sha_and_invalid_openpine_self_identity() -> None:
    lock = load_stack_lock()
    broken = copy.deepcopy(lock)
    broken["components"][1]["commit"] = "0" * 40
    broken["components"][0]["tree_sha256"] = "0" * 64

    errors = validate_stack_lock(broken)

    assert any("nonzero immutable SHA" in error for error in errors)
    assert any("tree_sha256" in error for error in errors)


def test_stack_lock_summary_is_endpoint_safe() -> None:
    summary = stack_lock_summary()
    assert set(summary) == {
        "schema",
        "release",
        "sha256",
        "source_tree_sha256",
        "source_tree_matches",
        "components",
    }
    assert len(summary["source_tree_sha256"]) == 64
    assert summary["source_tree_matches"] is (
        summary["source_tree_sha256"] == summary["components"][0]["tree_sha256"]
    )
    assert len(summary["components"]) == 7
    assert set(summary["components"][0]) == {
        "name",
        "version",
        "tree_sha256",
        "contracts",
    }
    assert all(
        set(item) == {"name", "version", "commit", "contracts"}
        for item in summary["components"][1:]
    )


def test_isolated_installed_wheel_serves_api_version_with_packaged_lock(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheel_dir), str(root)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("openpine-*.whl"))
    target = tmp_path / "site-packages"
    with zipfile.ZipFile(wheel) as archive:
        members = [PurePosixPath(item.filename) for item in archive.infolist()]
        assert all(not member.is_absolute() and ".." not in member.parts for member in members)
        archive.extractall(target)
    smoke = f"""
import sys
sys.path.insert(0, {str(target)!r})
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpine.gateway.deps import get_state
from openpine.gateway.routes.version import router

app = FastAPI()
app.include_router(router, prefix='/api')
app.dependency_overrides[get_state] = lambda: SimpleNamespace()
response = TestClient(app).get('/api/version')
assert response.status_code == 200, response.text
payload = response.json()
assert payload['stack_lock']['release'] == '4.0.1'
assert len(payload['stack_lock']['sha256']) == 64
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", smoke],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": ""},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
