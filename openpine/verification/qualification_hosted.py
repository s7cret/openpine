"""Bounded hosted driver. Provisioning belongs to the separately approved workflow."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any

from openpine.verification.protected_qualification import (
    FAULTS, MODES, read_json, run_fault, sha256, write_primary,
)
from openpine.verification.qualification_public import (
    ERRORS, SHA, STAGES, declared_commits, project, replace_projection,
)


class QualificationFailure(ValueError):
    def __init__(self, code: str):
        if code not in ERRORS - {"none", "incomplete"}:
            raise ValueError("invalid diagnostic category")
        self.code = code
        super().__init__(code)


def error_code(error: BaseException, stage: str) -> str:
    if isinstance(error, QualificationFailure):
        return error.code
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "missing-input"
    if isinstance(error, OSError):
        return "io-failed"
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return "identity-mismatch" if stage == "identity" else "invalid-input"
    if isinstance(error, RuntimeError) and stage in {"prepare", "restore-A", "restore-B"}:
        return "command-failed"
    return "unexpected-error"


@dataclass
class DiagnosticState:
    host: Path
    work: Path
    candidate_sha: str | None
    public_approved: bool
    stage: str = "identity"
    failure: tuple[str, str] | None = None
    commits: dict[str, str] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    owners_passed: bool = False

    def publish(self, *, complete: bool = False) -> dict[str, Any]:
        stage, code = self.failure or (self.stage, "none" if complete else "incomplete")
        try:
            value = project(self.candidate_sha, self.commits, self.rows,
                self.host / "verification/protected-qualification-public-allowlist.json",
                owner_checks_passed=self.owners_passed, stage=stage, error=code)
        except (ValueError, KeyError, TypeError):
            # Malformed runtime output cannot prevent a closed failure report.
            self.failure = self.failure or (self.stage, "invalid-input")
            value = project(self.candidate_sha, {}, [],
                self.host / "verification/protected-qualification-public-allowlist.json",
                stage=self.failure[0], error=self.failure[1])
        if self.public_approved:
            replace_projection(self.work / "public", value)
        return value

    def at(self, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError("invalid diagnostic stage")
        self.stage = stage
        self.publish()

    def fail(self, code: str) -> None:
        if code not in ERRORS - {"none", "incomplete"}:
            raise ValueError("invalid diagnostic category")
        self.failure = self.failure or (self.stage, code)


def candidate_wheelhouse(source: dict[str, Any], wheelhouse: Path, output: Path) -> Path:
    """Select exact candidate bytes without weakening the strict finalizer."""
    from packaging.utils import InvalidWheelFilename, parse_wheel_filename
    from packaging.version import InvalidVersion, Version
    from scripts.finalize_stack_candidate import CandidateFinalizationError, _wheel_metadata, normalize_name
    from openpine.verification.qualification_public import COMPONENTS
    if set(source["components"]) != COMPONENTS:
        raise QualificationFailure("invalid-input")
    if wheelhouse.is_symlink():
        raise QualificationFailure("invalid-input")
    if not wheelhouse.is_dir():
        raise QualificationFailure("missing-input")
    expected = {normalize_name(name): row["version"] for name, row in source["components"].items()}
    selected: dict[str, Path] = {}
    for path in sorted(wheelhouse.glob("*.whl")):
        if path.is_symlink() or not path.is_file():
            raise QualificationFailure("invalid-input")
        try:
            name, version, _, _ = parse_wheel_filename(path.name)
            # Third-party wheels can include vendored .dist-info/METADATA.
            # Only candidate distributions enter the strict stack reader.
            if name not in expected:
                continue
            metadata_name, metadata_version, _ = _wheel_metadata(path)
            if (name in selected or metadata_name != name or metadata_version != expected[name]
                    or version != Version(expected[name])):
                raise QualificationFailure("invalid-input")
        except (InvalidWheelFilename, InvalidVersion, CandidateFinalizationError) as error:
            raise QualificationFailure("invalid-input") from error
        selected[name] = path
    if set(selected) != set(expected):
        raise QualificationFailure("missing-input")
    # Validate the full set before publishing any selected wheel bytes.
    output.mkdir(parents=True, exist_ok=False)
    for path in selected.values():
        os.link(path, output / path.name)
    return output


def require_output(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise QualificationFailure("missing-output")
    return read_json(path)


def clean_environment() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TZ": "UTC",
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", **{key: "1" for key in (
                "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}


def call(argv: list[str], cwd: Path, output: Path, *, source_host: Path | None = None) -> None:
    from openpine.verification.execution_process import run_logged
    if shutil.disk_usage(output.parent).free < 3000000000:
        raise QualificationFailure("disk-reserve")
    env = clean_environment()
    if source_host is not None:
        env["PYTHONPATH"] = str(source_host)
    result = run_logged(argv, cwd=cwd, output=output, env=env, timeout=2400)
    if not result["ok"]:
        raise QualificationFailure("timeout" if result.get("status") == "timeout" else "command-failed")


def execute(spec: Path, folder: Path) -> int:
    # Running under each actual restored -I candidate interpreter is mandatory.
    binding = read_json(spec)
    if Path(sys.prefix).resolve() != Path(binding["prefix"]).resolve():
        raise ValueError("qualification is not running in its bound installed placement")
    if binding["placement"] not in {"A", "B"}:
        raise ValueError("unknown qualification placement")
    rows, complete = [], False
    try:
        for mode in MODES:
            for fault in FAULTS:
                row = run_fault(spec, folder / (mode + "-" + fault), mode, fault)
                row["placement"] = binding["placement"]
                rows.append(row)
                # Each returned observation survives a later exception or a
                # killed matrix process; no automatic primary is rewritten.
                write_primary(folder / ("case-" + mode + "-" + fault + ".json"), row)
        complete = True
    finally:
        write_primary(folder / "matrix.json", {"cases": rows, "complete": complete})
    return 0 if all(r["ok"] for r in rows) else 1


def collect_matrix_results(state: DiagnosticState, folder: Path, placement: str) -> None:
    """Retain validated closed observations before rejecting incomplete output."""
    if folder.is_symlink() or not folder.is_dir() or placement not in {"A", "B"}:
        raise QualificationFailure("invalid-input")

    def checkpoints() -> Iterator[dict[str, Any]]:
        for mode in MODES:
            for fault in FAULTS:
                path = folder / ("case-" + mode + "-" + fault + ".json")
                if path.is_symlink() or path.exists() and not path.is_file():
                    raise QualificationFailure("invalid-input")
                if path.is_file():
                    row = read_json(path)
                    if row.get("mode") != mode or row.get("fault") != fault:
                        raise QualificationFailure("invalid-input")
                    yield row

    matrix_path = folder / "matrix.json"
    if matrix_path.is_symlink() or matrix_path.exists() and not matrix_path.is_file():
        raise QualificationFailure("invalid-input")
    if matrix_path.is_file():
        matrix = require_output(matrix_path)
        values = matrix.get("cases")
        complete = matrix.get("complete", isinstance(values, list) and len(values) == 6)
        if not isinstance(values, list):
            raise QualificationFailure("invalid-input")
    else:
        values, complete = checkpoints(), False
    expected = {(placement, mode, fault) for mode in MODES for fault in FAULTS}
    observed = set()
    for row in values:
        try:
            identity = tuple(row.get(key) for key in ("placement", "mode", "fault"))
            if identity not in expected:
                raise ValueError("foreign matrix identity")
            value = project(state.candidate_sha, state.commits, [*state.rows, row],
                state.host / "verification/protected-qualification-public-allowlist.json",
                stage=state.stage, error="incomplete")
        except (AttributeError, ValueError, KeyError, TypeError) as error:
            raise QualificationFailure("invalid-input") from error
        # Store only permitted fields. A later invalid row cannot erase this
        # already validated prefix or copy raw per-fault errors to the public.
        state.rows[:] = value["cases"]
        observed.add(identity)
    if type(complete) is not bool:
        raise QualificationFailure("invalid-input")
    if not complete or observed != expected:
        raise QualificationFailure("missing-output")


def _prepare_candidate(host: Path, work: Path, approved_sha: str, state: DiagnosticState) -> tuple[Path, Path, Path, dict[str, str]]:
    from openpine.verification.execution_ci import prepare
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
        raise QualificationFailure("unsupported-runtime")
    if shutil.disk_usage(work.parent).free < 3000000000:
        raise QualificationFailure("disk-reserve")
    private = work / "private"
    private.mkdir(mode=0o700)
    prepared = private / "prepare"
    state.at("prepare")
    prepare(host, prepared, "3.13")
    bundle = prepared / "bundle"
    state.at("candidate-manifest")
    source = materialize_candidate(read_json(host / "candidates/stack-candidate-5.0.0-rc.6.template.json"),
        openpine_sha=actual, created_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        provenance={"builder": "run04-hosted-protected", "run_id": os.environ.get("GITHUB_RUN_ID", "local")})
    state.at("candidate-wheels")
    exact_wheels = candidate_wheelhouse(source, bundle / "wheelhouse", private / "candidate-wheelhouse")
    state.at("candidate-manifest")
    try:
        manifest = finalize_candidate(source, exact_wheels)
    except RuntimeError as error:
        raise QualificationFailure("invalid-input") from error
    manifest_path = private / "candidate.json"
    write_primary(manifest_path, manifest)
    commits = {n: c["sha"] for n, c in manifest["components"].items()}
    if state.commits != commits:
        raise QualificationFailure("identity-mismatch")
    return bundle, exact_wheels, manifest_path, commits


def _prepare_and_run(host: Path, work: Path, approved_sha: str, state: DiagnosticState, *, preflight: bool = False) -> int:
    from openpine.verification.execution_ci import restore, verify_bundle
    from openpine.verification.pytest_gate import validate_inventory, validate_phase_reports

    bundle, exact_wheels, manifest_path, commits = _prepare_candidate(host, work, approved_sha, state)
    private = work / "private"
    if preflight:
        report = verify_bundle(bundle)
        if report["host_commit"] != approved_sha or report["source_pins"] != {n: c for n, c in commits.items() if n != "openpine"}:
            raise QualificationFailure("identity-mismatch")
        files = [{"filename": path.name, "sha256": sha256(path), "size": path.stat().st_size}
                 for path in sorted((bundle / "wheelhouse").iterdir())]
        write_primary(private / "candidate-preflight.json", {"ok": True,
            "scope": "real candidate preparation and finalization only; no restore or protected worker",
            "source_commits": commits, "environment": report["environment"],
            "wheelhouse_files": files, "selected_wheels": sorted(p.name for p in exact_wheels.iterdir()),
            "candidate_manifest_sha256": sha256(manifest_path),
            "raw_primaries_durable": False, "full_qualification_accepted": False})
        return 0
    state.at("test-namespace")
    tests_root = private / "test-namespace"
    tests_root.mkdir()
    for namespace in ("rc6_tests", "int05_tests"):
        shutil.copytree(host / namespace, tests_root / namespace)
    shutil.copy2(host / "pyproject.toml", tests_root / "pyproject.toml")
    shutil.copytree(host / "verification", tests_root / "verification")
    failures = []
    policy = read_json(host / "verification/execution-policy.json")
    protected = policy["stabilization"]["protected-workers"]["nodes"]["openpine"]
    write_primary(private / "protected-denominator.json", {"nodeids": protected, "count": len(protected)})
    for placement in ("A", "B"):
        # Installed public package bytes must be traversable by the dedicated
        # sandbox user; raw receipts/specs remain under the separate 0700 tree.
        root = work / ("placement-" + placement)
        state.at("restore-" + placement)
        restore(bundle, root)
        restored = require_output(root / "restored.json")
        python = restored["executable"]
        spec = private / ("binding-" + placement + ".json")
        write_primary(spec, {"placement": placement, "prefix": str(Path(python).parent.parent),
            "tests_root": str(tests_root), "candidate_manifest": str(manifest_path),
            "candidate_manifest_sha256": sha256(manifest_path), "wheelhouse": str(exact_wheels),
            "source_commits": commits})
        output = private / ("faults-" + placement)
        output.mkdir()
        state.at("matrix-" + placement)
        try:
            call([python, "-I", "-B", "-m", "openpine.verification.qualification_hosted", "execute",
                  "--spec", str(spec), "--work", str(output)], work, private / ("matrix-command-" + placement))
        except QualificationFailure as error:
            failures.append("matrix-" + placement)
            state.fail(error.code)
        collect_matrix_results(state, output, placement)
        # Exact copied test namespace, installed product modules, external cwd.
        for label, selectors in (("int04", ["rc6_tests/test_rc6_int04_lifecycle.py"]),
                                 ("int05-owner-contract", ["int05_tests"]),
                                 ("protected", protected)):
            state.at(("int05" if label == "int05-owner-contract" else label) + "-" + placement)
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
                receipt = require_output(private / (label + placement + ".json"))
                locks = read_json(host / "verification/protected-qualification-inventory.json")
                validate_inventory(receipt["nodeids"], locks[label], receipt["deselected"])
                if (receipt.get("ok") is not True or receipt.get("collect_only") is not False
                        or validate_phase_reports(receipt["nodeids"], receipt["reports"])):
                    raise QualificationFailure("invalid-input")
            except QualificationFailure as error:
                failures.append(label + placement)
                state.fail(error.code)
    state.owners_passed = not any(not name.startswith("matrix-") for name in failures)
    state.at("outcome")
    write_primary(private / "outcome.json", {"ok": not failures,
        "failed_phases": failures, "raw_primaries_durable": False, "full_qualification_accepted": False})
    state.stage = "complete"
    projection = state.publish(complete=True)
    return 0 if projection["ok"] else 1


def prepare_and_run(host: Path, work: Path, approved_sha: str, public_approved: bool, *, preflight: bool = False) -> int:
    state = DiagnosticState(host, work, approved_sha if SHA.fullmatch(approved_sha) else None, public_approved)
    owned = False
    try:
        work.mkdir(parents=True, exist_ok=False)
        owned = True
        # A closed incomplete checkpoint exists before any preparation command.
        state.publish()
        state.commits = declared_commits(host, state.candidate_sha)
        state.publish()
        if preflight:
            if public_approved:
                raise QualificationFailure("invalid-input")
            return _prepare_and_run(host, work, approved_sha, state, preflight=True)
        return _prepare_and_run(host, work, approved_sha, state)
    except (Exception, KeyboardInterrupt, SystemExit) as error:  # noqa: BLE001 -- closed CLI boundary: never exports exception text or private diagnostics
        if not owned:
            return 1
        state.fail(error_code(error, state.stage))
        try:
            private = work / "private"
            if private.is_symlink():
                raise ValueError("unsafe private diagnostic directory")
            private.mkdir(mode=0o700, exist_ok=True)
            write_primary(private / "driver-failure.json", {"stage": state.stage,
                "error": state.failure[1] if state.failure else "unexpected-error",
                "traceback": "".join(traceback.format_exception(error))[-1024 * 1024:]})
        except (OSError, ValueError, TypeError):
            pass  # Private persistence failure cannot prevent the public guard.
        try:
            state.publish()
        except (OSError, ValueError, KeyError, TypeError):
            # The independent workflow fallback handles absent/partial files.
            return 1
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "execute", "preflight"))
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
    return prepare_and_run(args.host, args.work, args.approved_sha, args.public_approved, preflight=args.action == "preflight")


if __name__ == "__main__":
    raise SystemExit(main())
