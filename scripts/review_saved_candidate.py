"""Collect baseline/candidate inventories and run an explicitly provisional review.

No source, release ref, locked inventory, assertion or expected value is rewritten.
A successful run is evidence for review, not automatic acceptance or publication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = "4a84ff2873caef95d8eb986dc1e999c379f33fbf"
CANDIDATE = "8aa6a3e4f6f172652daf3ba37aade9f21e122c6c"
REPOS = ("openpine-contracts", "pinelib", "pine2ast", "ast2python", "backtest_engine", "optimizer", "marketdata-provider", "openpine")
HOST = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
TEMP = Path(os.environ["RUNNER_TEMP"]).resolve()
OUT = TEMP / "candidate-review"
OUT.mkdir(exist_ok=True)
STACK = TEMP / "review-stack"
STACK.mkdir(exist_ok=True)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def execute(label, command, cwd, env=None, timeout=1500):
    path = OUT / (label + ".log")
    print("::group::" + label, flush=True)
    try:
        with path.open("w") as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                code = 124
    except OSError as error:
        path.write_text(str(error))
        code = 125
    print(path.read_text(errors="replace")[-18000:], flush=True)
    print("::endgroup::", flush=True)
    return code


def pins(ref):
    value = json.loads(git(HOST, "show", ref + ":docs/RC6_LIFECYCLE_SOURCES.json"))
    if set(value) != set(REPOS) - {"openpine"} or not all(re.fullmatch(r"[0-9a-f]{40}", x) for x in value.values()):
        raise ValueError("invalid source pins")
    return {**value, "openpine": ref}


def prepare(kind, refs):
    sources = []
    for name in REPOS:
        source = STACK / name
        if not source.exists():
            subprocess.run(["git", "clone", "--quiet", "https://github.com/s7cret/" + name + ".git", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", refs[name]], check=True)
        if git(source, "rev-parse", "HEAD") != refs[name]:
            raise ValueError("source identity mismatch")
        clean = TEMP / ("install-" + kind) / name
        clean.mkdir(parents=True)
        data = subprocess.check_output(["git", "-C", str(source), "archive", "HEAD"])
        subprocess.run(["tar", "-x", "-C", str(clean)], input=data, check=True)
        extra = "[dev]" if name in {"openpine", "optimizer"} else "[parquet,stream,zstd]" if name == "marketdata-provider" else ""
        sources.append(str(clean) + extra)
        if kind == "candidate":
            subprocess.run(["git", "-C", str(source), "archive", "--format=tar.gz", "--output=" + str(OUT / (name + ".source.tar.gz")), "HEAD"], check=True)
    if execute("install-" + kind, [sys.executable, "-m", "pip", "install", *sources], HOST):
        raise RuntimeError("source installation failed")
    write(OUT / (kind + "-pins.json"), refs)


def command(name, kind, collect=False):
    root = STACK / name
    output = OUT / (kind + "-" + name + ".inventory.json")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "openpine.verification.pytest_gate", "--verification-suite=" + name, "--verification-output=" + str(output)]
    if kind == "baseline":
        cmd += ["--verification-lock=" + str(STACK / "openpine/verification/inventory.json")]
    if collect:
        cmd += ["--collect-only"]
    else:
        cmd += ["--junitxml=" + str(OUT / (name + ".xml"))]
    if name == "marketdata-provider":
        cmd += ["-m", "not live_network"]
    if name == "openpine":
        selected = json.loads((root / "rc6_tests/selected_regressions.json").read_text())
        if len(set(selected)) != len(selected) or any(not re.fullmatch(r"tests/test_[^/]+\.py", x) or not (root / x).is_file() for x in selected):
            raise ValueError("invalid host regression selection")
        cmd += ["-p", "pytest_asyncio.plugin", "rc6_tests", *sorted(selected)]
    return cmd, root, output


def main():
    baseline, candidate = pins(BASE), pins(CANDIDATE)
    prepare("baseline", baseline)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    baseline_ids = {}
    for name in REPOS:
        cmd, root, receipt = command(name, "baseline", True)
        if execute("collect-baseline-" + name, cmd, root, env):
            raise RuntimeError("baseline collection/lock mismatch: " + name)
        baseline_ids[name] = json.loads(receipt.read_text())
    prepare("candidate", candidate)
    review, proposal = {}, {}
    for name in REPOS:
        cmd, root, receipt = command(name, "candidate", True)
        if execute("collect-candidate-" + name, cmd, root, env):
            raise RuntimeError("candidate collection failed: " + name)
        actual = json.loads(receipt.read_text())
        previous = baseline_ids[name]
        missing = sorted(set(previous["nodeids"]) - set(actual["nodeids"]))
        if missing or actual["deselected"] != previous["deselected"]:
            write(OUT / "inventory-rejected.json", {"repository": name, "missing": missing, "baseline_deselected": previous["deselected"], "candidate_deselected": actual["deselected"]})
            raise ValueError("candidate silently shrinks or deselects baseline tests")
        proposal[name] = {k: actual[k] for k in ("count", "sha256", "deselected")}
        review[name] = {"baseline": previous["count"], "candidate": actual["count"], "removed": missing, "added": sorted(set(actual["nodeids"]) - set(previous["nodeids"]))}
    write(OUT / "proposed-inventory.json", proposal)
    write(OUT / "inventory-review.json", review)
    results = {}
    env["OPENPINE_STAGE1_EVIDENCE"] = str(OUT / "corpus-evidence")
    env["PINE_STACK_ROOT"] = str(STACK)
    for name in REPOS:
        cmd, root, receipt = command(name, "execution")
        code = execute("tests-" + name, cmd, root, env)
        facts = {"exit_code": code, "xml_present": (OUT / (name + ".xml")).is_file()}
        if facts["xml_present"]:
            cases = list(ET.parse(OUT / (name + ".xml")).iter("testcase"))
            facts.update({"cases": len(cases), "failures": sum(c.find("failure") is not None for c in cases), "errors": sum(c.find("error") is not None for c in cases), "skipped": sum(c.find("skipped") is not None for c in cases)})
        if receipt.exists():
            actual = json.loads(receipt.read_text())
            facts["inventory_exact"] = all(actual[k] == proposal[name][k] for k in ("count", "sha256", "deselected"))
            facts["all_phases_passed"] = actual["ok"] and not actual["collect_only"]
        results[name] = facts
        write(OUT / "test-summary.json", results)
    arch = execute("architecture", [sys.executable, "-m", "openpine.verification", "architecture", "--stack-root", str(STACK), "--output", str(OUT / "architecture.json")], STACK / "openpine", env)
    api = execute("openapi", [sys.executable, "scripts/export_openapi.py", str(OUT / "openapi.json")], STACK / "openpine", env)
    builds = {}
    for name in REPOS:
        builds[name] = execute("build-" + name, [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(OUT / "dist" / name), str(TEMP / "install-candidate" / name)], HOST, env)
    clean = {name: not git(STACK / name, "status", "--porcelain", "--untracked-files=no") for name in REPOS}
    write(OUT / "review-summary.json", {"schema_id": "openpine.saved_candidate_review.v1", "baseline": BASE, "candidate": CANDIDATE, "harness": os.environ["GITHUB_SHA"], "python": sys.version, "inventory_count": sum(r["count"] for r in proposal.values()), "tests": results, "architecture_exit": arch, "openapi_exit": api, "builds": builds, "tracked_sources_clean": clean, "full_stage2_accepted": False, "release_updated": False, "purpose": "diagnostic_execution_pending_independent_inventory_and_semantic_review"})
    ok = all(v.get("all_phases_passed") and v.get("inventory_exact") and v["exit_code"] == 0 for v in results.values()) and arch == api == 0 and not any(builds.values()) and all(clean.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
