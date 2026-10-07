"""Bounded hosted driver. Provisioning belongs to the separately approved workflow."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from openpine.verification.protected_qualification import (
    FAULTS, MODES, read_json, run_fault, sha256, write_primary,
)
from openpine.verification.qualification_public import project, write_projection


def clean_environment() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TZ": "UTC",
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", **{key: "1" for key in (
                "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}


def call(argv: list[str], cwd: Path, output: Path, *, source_host: Path | None = None) -> None:
    from openpine.verification.execution_process import run_logged
    if shutil.disk_usage(output.parent).free < 3000000000:
        raise ValueError("3 GB reserve refused qualification phase before launch")
    env = clean_environment()
    if source_host is not None:
        env["PYTHONPATH"] = str(source_host)
    result = run_logged(argv, cwd=cwd, output=output, env=env, timeout=2400)
    if not result["ok"]:
        raise ValueError("logged qualification phase failed; private receipt retained")


def execute(spec: Path, folder: Path) -> int:
    # Running under each actual restored -I candidate interpreter is mandatory.
    binding = read_json(spec)
    if Path(sys.prefix).resolve() != Path(binding["prefix"]).resolve():
        raise ValueError("qualification is not running in its bound installed placement")
    rows = []
    for mode in MODES:
        for fault in FAULTS:
            row = run_fault(spec, folder / (mode + "-" + fault), mode, fault)
            row["placement"] = binding["placement"]
            rows.append(row)
    write_primary(folder / "matrix.json", {"cases": rows})
    return 0 if all(r["ok"] for r in rows) else 1


def prepare_and_run(host: Path, work: Path, approved_sha: str, public_approved: bool) -> int:
    from openpine.verification.execution_ci import prepare, restore
    from scripts.materialize_stack_candidate import materialize_candidate
    from scripts.finalize_stack_candidate import finalize_candidate
    from datetime import datetime, timezone

    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required for exact candidate identity")
    actual = subprocess.check_output([git, "-C", str(host), "rev-parse", "HEAD"], text=True).strip()  # noqa: S603 -- resolved Git executable, fixed read-only argv, no shell
    if actual != approved_sha:
        raise ValueError("checked-out source differs from action-time approved SHA")
    if sys.version_info[:3] != (3, 13, 5) or not sys._is_gil_enabled():
        raise ValueError("ordinary CPython 3.13.5 with GIL is required")
    if shutil.disk_usage(work.parent).free < 3000000000:
        raise ValueError("3 GB disk reserve is unavailable")
    work.mkdir(parents=True, exist_ok=False)
    private = work / "private"
    private.mkdir(mode=0o700)
    prepared = private / "prepare"
    prepare(host, prepared, "3.13")
    bundle = prepared / "bundle"
    source = materialize_candidate(read_json(host / "candidates/stack-candidate-5.0.0-rc.6.template.json"),
        openpine_sha=actual, created_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        provenance={"builder": "run04-hosted-protected", "run_id": os.environ.get("GITHUB_RUN_ID", "local")})
    manifest = finalize_candidate(source, bundle / "wheelhouse")
    manifest_path = private / "candidate.json"
    write_primary(manifest_path, manifest)
    commits = {n: c["sha"] for n, c in manifest["components"].items()}
    tests_root = private / "test-namespace"
    tests_root.mkdir()
    for namespace in ("rc6_tests", "int05_tests"):
        shutil.copytree(host / namespace, tests_root / namespace)
    shutil.copy2(host / "pyproject.toml", tests_root / "pyproject.toml")
    shutil.copytree(host / "verification", tests_root / "verification")
    rows, failures = [], []
    policy = read_json(host / "verification/execution-policy.json")
    protected = policy["stabilization"]["protected-workers"]["nodes"]["openpine"]
    write_primary(private / "protected-denominator.json", {"nodeids": protected, "count": len(protected)})
    for placement in ("A", "B"):
        # Installed public package bytes must be traversable by the dedicated
        # sandbox user; raw receipts/specs remain under the separate 0700 tree.
        root = work / ("placement-" + placement)
        restore(bundle, root)
        restored = read_json(root / "restored.json")
        python = restored["executable"]
        spec = private / ("binding-" + placement + ".json")
        write_primary(spec, {"placement": placement, "prefix": str(Path(python).parent.parent),
            "tests_root": str(tests_root), "candidate_manifest": str(manifest_path),
            "candidate_manifest_sha256": sha256(manifest_path), "wheelhouse": str(bundle / "wheelhouse"),
            "source_commits": commits})
        output = private / ("faults-" + placement)
        output.mkdir()
        try:
            call([python, "-I", "-B", "-m", "openpine.verification.qualification_hosted", "execute",
                  "--spec", str(spec), "--work", str(output)], work, private / ("matrix-command-" + placement))
        except ValueError:
            failures.append("matrix-" + placement)
        matrix = output / "matrix.json"
        if matrix.exists():
            rows.extend(read_json(matrix)["cases"])
        # Exact copied test namespace, installed product modules, external cwd.
        for label, selectors in (("int04", ["rc6_tests/test_rc6_int04_lifecycle.py"]),
                                 ("int05-owner-contract", ["int05_tests"]),
                                 ("protected", protected)):
            stack_root = str(Path(restored["roots"]["openpine"]).parent)
            restored_host = Path(restored["roots"]["openpine"])
            argv = [python, "-I", "-B", "-m", "openpine.verification.qualification_test_driver",
                    "--tests-root", str(restored_host), "--stack-root", stack_root,
                    "--", "--import-mode=importlib", "-q", "-p", "no:cacheprovider",
                    "-p", "openpine.verification.pytest_gate", "--verification-output=" + str(private / (label + placement + ".json")),
                    "--verification-suite=" + label,
                    "--verification-lock=" + str(host / "verification/protected-qualification-inventory.json"),
                    "--basetemp=" + str(private / ("pytest-" + label + placement)),
                    "--junitxml=" + str(private / (label + placement + ".xml")), *selectors]
            for plugin in policy["components"]["openpine"].get("plugins", []):
                argv.extend(["-p", plugin])
            try:
                call(argv, restored_host, private / ("tests-" + label + placement))
            except ValueError:
                failures.append(label + placement)
    owners_passed = not any(not name.startswith("matrix-") for name in failures)
    projection = project(actual, commits, rows, host / "verification/protected-qualification-public-allowlist.json",
                         owner_checks_passed=owners_passed)
    if public_approved:
        write_projection(work / "public", projection)
    write_primary(private / "outcome.json", {"ok": not failures and projection["protected_matrix_passed"],
        "failed_phases": failures, "raw_primaries_durable": False, "full_qualification_accepted": False})
    return 0 if not failures and projection["protected_matrix_passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "execute"))
    parser.add_argument("--host", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--approved-sha")
    parser.add_argument("--public-approved", action="store_true")
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()
    if args.action == "execute":
        if args.spec is None:
            parser.error("execute requires --spec")
        return execute(args.spec, args.work)
    if args.host is None or args.approved_sha is None:
        parser.error("run requires --host and --approved-sha")
    return prepare_and_run(args.host, args.work, args.approved_sha, args.public_approved)


if __name__ == "__main__":
    raise SystemExit(main())
