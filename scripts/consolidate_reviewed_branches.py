"""Archive reviewed branch tips and remove only their compare-and-delete refs.

Never rewrites a kept branch. The single atomic push either creates every archive
and removes every reviewed branch, or does nothing when a ref changes concurrently.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

KEEP = frozenset({"main", "release/v2.17", "release/v4.0.2", "release/5.0.0rc6"})
OPS = "ops/rc6-delivery-branch-selection"
RETIRE = frozenset({OPS, "feat/5.0-isolated-worker", "fix/data-delete-semantic-profile",
    "fix/5.0-rc5-idempotent-semantic-profile-migration", "ops/rc5-immutable-deploy-identity",
    "release/5.0.0rc5", "release/5.0.0rc6-local-candidate"})
PIN_PATHS = frozenset({"candidates/stack-candidate-5.0.0-rc.5.template.json",
    "openpine/stack-lock.json", "tests/test_stack_lock.py"})


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def refs() -> dict[str, str]:
    return {ref: sha for sha, ref in (line.split() for line in
            git("ls-remote", "--refs", "origin").splitlines())}


def validate(plan: dict, current: dict[str, str], self_sha: str) -> tuple[dict, dict]:
    keep, retire = dict(plan["keep"]), dict(plan["retire"])
    if set(keep) != KEEP or set(retire) != RETIRE or retire.get(OPS) != "SELF":
        raise ValueError("branch allowlist mismatch")
    retire[OPS] = self_sha
    if not all(re.fullmatch("[0-9a-f]{40}", sha) for sha in [*keep.values(), *retire.values()]):
        raise ValueError("invalid branch identity")
    expected = {"refs/heads/" + name: sha for name, sha in {**keep, **retire}.items()}
    heads = {name: sha for name, sha in current.items() if name.startswith("refs/heads/")}
    if heads != expected:
        raise ValueError("branches changed or inventory is incomplete")
    for name, sha in retire.items():
        tag = current.get("refs/tags/" + name)
        if tag is not None and tag != sha:
            raise ValueError("archive tag already points elsewhere")
    return keep, retire


def verify_selection(plan: dict, keep: dict, retire: dict) -> None:
    rc6 = keep["release/5.0.0rc6"]
    git("merge-base", "--is-ancestor", plan["tested_head"], rc6)
    changes = git("diff", "--name-only", plan["tested_head"], rc6).splitlines()
    if any(not name.startswith(("docs/", ".github/", "scripts/consolidate_", "tests/test_branch_consolidation")) for name in changes):
        raise ValueError("untested runtime changes after verified code head")
    selected, excluded = plan["selected"], set(plan["excluded_rc5_pins"])
    for name, sha in retire.items():
        unique = git("rev-list", sha, "--not", *keep.values()).splitlines()
        for source in unique:
            changed = set(git("diff-tree", "--no-commit-id", "--name-only", "-r", source).splitlines())
            if name == OPS and changed and all(path.startswith(".github/") for path in changed):
                continue
            if source in excluded and changed and changed <= PIN_PATHS:
                continue
            target = selected.get(source)
            if target is None:
                raise ValueError("unreviewed unique commit: " + source)
            git("merge-base", "--is-ancestor", target, rc6)
            message = git("show", "-s", "--format=%B", target)
            if source[:7] not in message:
                raise ValueError("preservation commit lacks source attribution")


def archive(plan: dict, self_sha: str, output: Path, *, before_push=None) -> dict:
    current = refs()
    keep, retire = validate(plan, current, self_sha)
    verify_selection(plan, keep, retire)
    output.mkdir(parents=True, exist_ok=True)
    (output / "before.json").write_text(json.dumps(current, indent=2) + "\n")
    leases, specs = [], []
    for name, sha in sorted(retire.items()):
        branch, tag = "refs/heads/" + name, "refs/tags/" + name
        leases.extend([f"--force-with-lease={branch}:{sha}",
                       f"--force-with-lease={tag}:{current.get(tag, '')}"])
        specs.extend([f"{sha}:{tag}", f":{branch}"])
    if before_push is not None:
        before_push()
    git("push", "--atomic", *leases, "origin", *specs)
    after = refs()
    expected = {"refs/heads/" + name: sha for name, sha in keep.items()}
    if {name: sha for name, sha in after.items() if name.startswith("refs/heads/")} != expected:
        raise ValueError("post-publication branch verification failed")
    if any(after.get("refs/tags/" + name) != sha for name, sha in retire.items()):
        raise ValueError("archive verification failed")
    (output / "after.json").write_text(json.dumps(after, indent=2) + "\n")
    return after


def main() -> None:
    plan = json.loads(Path(sys.argv[1]).read_text())
    if (os.environ.get("GITHUB_REPOSITORY") != "s7cret/openpine"
            or os.environ.get("GITHUB_REF") != "refs/heads/" + OPS):
        raise ValueError("wrong repository or maintenance branch")
    url = "https://api.github.com/repos/s7cret/openpine/actions/runs/" + str(int(plan["verification_run"]))
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        verified = json.load(response)
    if verified["conclusion"] != "success" or verified["head_sha"] != plan["verification_trigger"]:
        raise ValueError("required integrated CI has not passed")
    git("fetch", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
    archive(plan, os.environ["GITHUB_SHA"], Path(sys.argv[2]))


if __name__ == "__main__":
    main()
