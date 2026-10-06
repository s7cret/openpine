from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
if GIT is None:
    raise RuntimeError("git executable not found")


def _load():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_candidate_wheelhouse",
        ROOT / "scripts" / "build_candidate_wheelhouse.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(  # noqa: S603
        [GIT, *args], cwd=path, text=True
    ).strip()


def _repo(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "ci@example.com")
    _git(path, "config", "user.name", "ci")
    (path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.0.1'\n",
        encoding="utf-8",
    )
    _git(path, "add", "pyproject.toml")
    _git(path, "commit", "-m", "init")
    return path


def test_sha_mismatch_is_fail_closed(tmp_path: Path) -> None:
    mod = _load()
    repo = _repo(tmp_path, "pinelib")
    sha = _git(repo, "rev-parse", "HEAD")
    candidate = {
        "components": {
            "pinelib": {"sha": "0" * 40},
        }
    }
    with pytest.raises(mod.CandidateError, match="sha mismatch"):
        mod.verify_checkouts(candidate, {"pinelib": repo})
    candidate["components"]["pinelib"]["sha"] = sha
    rows = mod.verify_checkouts(candidate, {"pinelib": repo})
    assert rows["pinelib"] == sha


def test_this_checkout_placeholder_is_rejected(tmp_path: Path) -> None:
    mod = _load()
    repo = _repo(tmp_path, "openpine")
    candidate = {"components": {"openpine": {"sha": "THIS_CHECKOUT"}}}

    with pytest.raises(mod.CandidateError, match="40 lowercase hex"):
        mod.verify_checkouts(candidate, {"openpine": repo})


def test_dirty_tree_is_fail_closed(tmp_path: Path) -> None:
    mod = _load()
    repo = _repo(tmp_path, "pinelib")
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "dirty.txt")
    candidate = {"components": {"pinelib": {"sha": _git(repo, "rev-parse", "HEAD")}}}
    with pytest.raises(mod.CandidateError, match="dirty"):
        mod.verify_checkouts(candidate, {"pinelib": repo})


def test_build_wheel_uses_running_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load()
    repo = _repo(tmp_path, "openpine")
    (repo / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools>=77','wheel']\n"
        "build-backend='setuptools.build_meta'\n"
        "[project]\nname='openpine'\nversion='0.0.1'\n"
        "[tool.setuptools.packages.find]\ninclude=['openpine*']\n",
        encoding="utf-8",
    )
    (repo / "openpine").mkdir()
    (repo / "openpine/__init__.py").write_text("answer = 42\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "build fixture")
    calls: list[list[str]] = []
    check_call = mod.subprocess.check_call

    def capture(argv):
        calls.append(argv)
        return check_call(argv)

    monkeypatch.setattr(mod.subprocess, "check_call", capture)
    output = tmp_path / "wheelhouse"
    report = mod.build_wheel(repo, output, no_isolation=True)
    builds = [argv for argv in calls if argv[0] == sys.executable]
    assert len(builds) == 1
    assert builds[0][:4] == [sys.executable, "-I", "-m", "build"]
    assert Path(builds[0][-1]).is_relative_to(output / "build-attempts")
    assert builds[0][-1] != str(repo)
    assert report["package_tree"]["files"].keys() == {"__init__.py"}
    assert (output / report["wheel"]).is_file()
