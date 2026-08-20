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


def test_this_checkout_resolves_to_head(tmp_path: Path) -> None:
    mod = _load()
    repo = _repo(tmp_path, "openpine")
    sha = _git(repo, "rev-parse", "HEAD")
    candidate = {"components": {"openpine": {"sha": "THIS_CHECKOUT"}}}
    rows = mod.verify_checkouts(candidate, {"openpine": repo})
    assert rows["openpine"] == sha


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
    calls: list[list[str]] = []
    monkeypatch.setattr(mod.subprocess, "check_call", lambda argv: calls.append(argv))

    mod.build_wheel(tmp_path / "source", tmp_path / "wheelhouse")

    assert calls == [
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(tmp_path / "wheelhouse"),
            str(tmp_path / "source"),
        ]
    ]
