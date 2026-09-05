"""Real local bare remotes exercise atomic archive/deletion and race protection."""
import copy
import runpy
from pathlib import Path
import subprocess

import pytest

M = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/consolidate_reviewed_branches.py"))
KEEP, RETIRE, OPS = M["KEEP"], M["RETIRE"], M["OPS"]


@pytest.fixture
def remote(tmp_path, monkeypatch):
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    monkeypatch.chdir(work)
    git = M["git"]
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (work / "file").write_text("base")
    git("add", "file"); git("commit", "-m", "base")
    sha = git("rev-parse", "HEAD")
    git("push", "origin", *[sha + ":refs/heads/" + name for name in KEEP | RETIRE])
    plan = {"keep": dict.fromkeys(KEEP, sha), "retire": {**dict.fromkeys(RETIRE, sha), OPS: "SELF"},
            "tested_head": sha, "selected": {}, "excluded_rc5_pins": []}
    return plan, sha, origin


def test_atomic_archive_keeps_four_branches_and_all_original_tips(remote, tmp_path):
    plan, sha, _ = remote
    after = M["archive"](plan, sha, tmp_path / "evidence")
    assert {name.removeprefix("refs/heads/") for name in after if name.startswith("refs/heads/")} == KEEP
    assert all(after["refs/tags/" + name] == sha for name in RETIRE)
    assert (tmp_path / "evidence/after.json").is_file()


@pytest.mark.parametrize("fault", ["new_branch", "moved_retire", "moved_keep", "tag_collision", "bad_allowlist", "bad_sha"])
def test_inventory_drift_fails_before_any_write(remote, tmp_path, fault):
    plan, sha, _ = remote
    before = M["refs"](); modified = copy.deepcopy(before)
    if fault == "new_branch": modified["refs/heads/new-work"] = sha
    elif fault == "moved_retire": modified["refs/heads/" + OPS] = "1" * 40
    elif fault == "moved_keep": modified["refs/heads/main"] = "1" * 40
    elif fault == "tag_collision": modified["refs/tags/" + OPS] = "1" * 40
    elif fault == "bad_allowlist": plan["retire"]["main"] = sha
    else: plan["keep"]["main"] = "not-a-sha"
    with pytest.raises(ValueError): M["validate"](plan, modified, sha)
    assert M["refs"]() == before


def test_concurrent_branch_commit_aborts_whole_transaction(remote, tmp_path):
    plan, sha, origin = remote
    git = M["git"]
    (Path.cwd() / "file").write_text("concurrent")
    git("commit", "-am", "concurrent")
    new = git("rev-parse", "HEAD")
    git("push", "origin", new + ":refs/heads/transient")
    git("push", "origin", ":refs/heads/transient")
    before = M["refs"]()
    def race():
        subprocess.run(["git", "--git-dir=" + str(origin), "update-ref", "refs/heads/" + OPS, new, sha], check=True)
    with pytest.raises(subprocess.CalledProcessError):
        M["archive"](plan, sha, tmp_path / "evidence", before_push=race)
    after = M["refs"]()
    assert len([x for x in after if x.startswith("refs/heads/")]) == 11
    assert not any(x.startswith("refs/tags/") for x in after)
    assert after["refs/heads/" + OPS] == new
    assert all(after[key] == value for key, value in before.items() if key != "refs/heads/" + OPS)


def test_unique_unreviewed_code_blocks_deletion(remote, tmp_path):
    plan, sha, _ = remote
    git = M["git"]
    Path("file").write_text("unreviewed feature")
    git("commit", "-am", "feature")
    new = git("rev-parse", "HEAD")
    name = "fix/data-delete-semantic-profile"
    git("push", "origin", new + ":refs/heads/" + name)
    plan["retire"][name] = new
    before = M["refs"]()
    with pytest.raises(ValueError, match="unreviewed"):
        M["archive"](plan, sha, tmp_path / "evidence")
    assert M["refs"]() == before
