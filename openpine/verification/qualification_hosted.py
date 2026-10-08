"""Bounded hosted driver. Provisioning belongs to the separately approved workflow."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
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
        value: dict[str, Any]
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
    value: dict[str, Any] = read_json(path)
    return value


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


def archive_primaries(host: Path, work: Path, output: Path, *, approved_sha: str,
                     run_id: str, expected_source_hash: str,
                     expected_candidate_manifest_sha256: str,
                     max_bytes: int, retention_days: int) -> dict:
    """Archive existing hosted primaries locally; this never runs qualification.

    Expected identities must be supplied independently of these downloaded
    inputs. Runs without the mandatory prepared/candidate binding are rejected;
    their original failure diagnostics remain untouched.
    """
    from openpine.admission import candidate_manifest_hash
    from openpine.verification.execution_ci import BUNDLE_SCHEMA, attest_ci_source_commits
    from openpine.verification.execution_evidence import (
        HASH, MANIFEST_LIMIT, MAX_FILES, _directory, archive_evidence,
    )
    from openpine.verification.execution_identity import evidence_path, hash_file
    from openpine.verification.identity import read_json as strict_json, verify

    host, work = _directory(host), _directory(work)
    if any(path.name not in {"private", "public", "placement-A", "placement-B"}
           for path in work.iterdir()):
        raise ValueError("unknown hosted primary root entry")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("hosted primary archive output must be a new private directory")
    if (not isinstance(approved_sha, str) or not SHA.fullmatch(approved_sha)
            or not isinstance(run_id, str) or not run_id or len(run_id) > 200
            or not isinstance(expected_source_hash, str) or not HASH.fullmatch(expected_source_hash)
            or not isinstance(expected_candidate_manifest_sha256, str)
            or not HASH.fullmatch(expected_candidate_manifest_sha256)
            or type(max_bytes) is not int or max_bytes <= 0
            or type(retention_days) is not int or not 1 <= retention_days <= 90):
        raise ValueError("invalid independently supplied archive identity or budget")
    commits = declared_commits(host, approved_sha)
    candidate_path = evidence_path(work, "private/candidate.json")
    bundle_path = evidence_path(work, "private/prepare/bundle/bundle.json")
    identity_files = {candidate_path: expected_candidate_manifest_sha256, bundle_path: hash_file(bundle_path)}
    candidate = strict_json(candidate_path)
    bundle = strict_json(bundle_path)
    verify(bundle, BUNDLE_SCHEMA)
    verify(bundle["source"], "openpine.execution_sources.v1")
    if (not isinstance(candidate, dict) or not isinstance(candidate.get("provenance"), dict)
            or not isinstance(candidate.get("components"), dict)
            or any(not isinstance(row, dict) for row in candidate["components"].values())):
        raise ValueError("invalid hosted candidate binding")
    if (hash_file(candidate_path) != expected_candidate_manifest_sha256
            or candidate.get("schema") != "openpine.stack-candidate.v2"
            or candidate.get("stage") != "wheel-bound" or candidate.get("not_a_release") is not True
            or candidate.get("manifest_hash") != candidate_manifest_hash(candidate)
            or candidate.get("provenance", {}).get("run_id") != run_id
            or {n: row["sha"] for n, row in candidate["components"].items()} != commits
            or attest_ci_source_commits([bundle]) != commits
            or bundle["source"]["content_hash"] != expected_source_hash):
        raise ValueError("hosted primary candidate/source/run identity mismatch")
    for placement in ("A", "B"):
        binding_path = evidence_path(work, "private/binding-" + placement + ".json", must_exist=False)
        restored_path = evidence_path(work, "placement-" + placement + "/restored.json", must_exist=False)
        if binding_path.exists():
            identity_files[binding_path] = hash_file(binding_path)
            binding = strict_json(binding_path)
            if (not isinstance(binding, dict) or binding.get("placement") != placement or binding.get("source_commits") != commits
                    or binding.get("candidate_manifest") != str(candidate_path)
                    or binding.get("candidate_manifest_sha256") != expected_candidate_manifest_sha256):
                raise ValueError("hosted primary placement binding mismatch")
        if restored_path.exists():
            identity_files[restored_path] = hash_file(restored_path)
            restored = strict_json(restored_path)
            if (not isinstance(restored, dict) or restored.get("candidate_hash") != expected_source_hash
                    or restored.get("source_commits") != commits):
                raise ValueError("hosted primary restored source identity mismatch")
    terminals = [evidence_path(work, "private/" + name, must_exist=False)
                 for name in ("outcome.json", "driver-failure.json", "candidate-preflight.json")]
    if not any(path.is_file() for path in terminals):
        raise ValueError("hosted primary archive requires a terminal success or failure diagnostic")
    for path in terminals:
        if path.is_file():
            value = strict_json(path)
            if (not isinstance(value, dict) or value.get("raw_primaries_durable", False) is not False
                    or value.get("full_qualification_accepted", False) is not False):
                raise ValueError("hosted primary terminal cannot claim durable or full acceptance")
            if path.name == "outcome.json" and value.get("ok") is True:
                for placement in ("A", "B"):
                    for label in ("int04", "int05-owner-contract", "protected"):
                        for suffix in (".json", ".xml"):
                            evidence_path(work, "private/" + label + placement + suffix)
                        evidence_path(work, "private/tests-" + label + placement + "/command.json")
                    evidence_path(work, "private/binding-" + placement + ".json")
                    evidence_path(work, "placement-" + placement + "/restored.json")
                    evidence_path(work, "private/matrix-command-" + placement + "/command.json")
                    evidence_path(work, "private/faults-" + placement + "/matrix.json")
                    for mode in MODES:
                        for fault in FAULTS:
                            case = "private/faults-" + placement + "/" + mode + "-" + fault
                            for primary in ("automatic-result.json", "forced-disposal.json", "before.json", "after.json",
                                            "neighbour/native-result.json"):
                                evidence_path(work, case + "/" + primary)
                            evidence_path(work, "private/faults-" + placement + "/case-" + mode + "-" + fault + ".json")
                            for role in ("affected", "neighbour"):
                                for primary in ("allocated-unit.json", "ready.json", "controller.stdout", "controller.stderr",
                                                "command/stdout.log", "command/stderr.log", "command/process-family.json",
                                                "command/input-0.bin"):
                                    evidence_path(work, case + "/" + role + "/" + primary)
                                if role == "neighbour" or fault != "controller-sigkill":
                                    evidence_path(work, case + "/" + role + "/command/command.json")

    def inventory() -> dict:
        files: dict[str, dict] = {}
        total = 0

        def add(path: Path, expected_hash: str | None = None) -> None:
            nonlocal total
            relative = path.relative_to(work).as_posix()
            path = evidence_path(work, relative)
            if relative not in files:
                size = path.stat().st_size
                total += size
                if total > max_bytes or len(files) >= MAX_FILES:
                    raise ValueError("hosted primary inventory exceeds archive budget")
                files[relative] = {"size": size, "sha256": hash_file(path)}
            if expected_hash is not None and files[relative]["sha256"] != expected_hash:
                raise ValueError("hosted referenced primary checksum mismatch")

        def tree(path: Path) -> None:
            _directory(path)
            for directory, subdirs, filenames in os.walk(path, followlinks=False):
                for name in subdirs:
                    _directory(Path(directory) / name)
                for name in sorted(filenames):
                    add(Path(directory) / name)

        # Fixed owner locations, with only its known material/fixture trees
        # omitted. Any referenced file in such a tree is added below.
        private = _directory(work / "private")
        for path in sorted(private.iterdir()):
            if path.is_file() or path.is_symlink():
                add(path)
            elif path.name in {"candidate-wheelhouse", "test-namespace", "prepare"} or re.fullmatch(
                    r"pytest-(?:int04|int05-owner-contract|protected)[AB]", path.name):
                continue
            elif re.fullmatch(r"(?:faults-|matrix-command-)[AB]|tests-(?:int04|int05-owner-contract|protected)[AB]", path.name):
                tree(path)
            else:
                raise ValueError("unknown hosted private primary directory")
        prepare = _directory(private / "prepare")
        if (prepare / "commands").exists():
            tree(prepare / "commands")
        prepared = _directory(prepare / "bundle")
        for path in sorted(prepared.iterdir()):
            if path.name in {"sources.tar.gz", "wheelhouse", "git"}:
                continue
            if path.is_file() or path.is_symlink():
                add(path)
            elif path.name == "collection.evidence":
                tree(path)
            else:
                raise ValueError("unknown prepared primary directory")
        for placement in ("A", "B"):
            root = work / ("placement-" + placement)
            if root.exists():
                _directory(root)
                if (root / "commands").exists():
                    tree(root / "commands")
                if (root / "restored.json").exists():
                    add(root / "restored.json")
        for path, digest in identity_files.items():
            add(path, digest)

        def described(value):
            if isinstance(value, dict):
                if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
                    if not HASH.fullmatch(value["sha256"]):
                        raise ValueError("invalid hosted referenced primary checksum")
                    yield value["path"], value["sha256"]
                for child in value.values():
                    yield from described(child)
            elif isinstance(value, list):
                for child in value:
                    yield from described(child)

        checked: set[str] = set()
        while set(files) - checked:
            for relative in sorted(set(files) - checked):
                checked.add(relative)
                if not relative.endswith(".json"):
                    continue
                path = evidence_path(work, relative)
                mandatory_owner = path.name in {"command.json", "execution.json"}
                if files[relative]["size"] > MANIFEST_LIMIT:
                    if mandatory_owner:
                        raise ValueError("unbounded hosted primary reference owner")
                    continue
                try:
                    value = strict_json(path)
                except (ValueError, UnicodeError, RecursionError):
                    if mandatory_owner:
                        raise ValueError("unreadable hosted primary reference owner") from None
                    continue  # Preserve incomplete raw failure diagnostics verbatim.
                if not isinstance(value, dict):
                    if mandatory_owner:
                        raise ValueError("nonobject hosted primary reference owner")
                    continue
                if path.name == "command.json":
                    if value.get("schema_id") != "openpine.execution_command.v1":
                        raise ValueError("invalid hosted primary reference owner schema")
                    verify(value, "openpine.execution_command.v1")
                elif path.name == "execution.json":
                    # The native campaign receipt is intentionally unsealed and
                    # has no schema_id. Require its existing reference shape.
                    if ("schema_id" in value or not isinstance(value.get("artifacts"), dict)
                            or any(not isinstance(value.get(key), str) or not value[key]
                                   for key in ("task", "shard", "attempt_id", "run_id", "cwd"))
                            or value.get("status") not in {"completed", "failed", "timeout", "cancelled", "infrastructure_error", "not_run"}
                            or (value.get("returncode") is not None and type(value["returncode"]) is not int)
                            or not isinstance(value.get("argv"), list) or not value["argv"]
                            or any(not isinstance(arg, str) for arg in value["argv"])
                            or any(not isinstance(desc, dict) or not isinstance(desc.get("path"), str)
                                   or not isinstance(desc.get("sha256"), str)
                                   for desc in value["artifacts"].values())):
                        raise ValueError("invalid hosted primary reference owner shape")
                if value.get("schema_id") == "openpine.execution_command.v1":
                    verify(value, "openpine.execution_command.v1")
                    if (not isinstance(value.get("files"), dict) or not isinstance(value.get("argv"), list)
                            or any(not isinstance(arg, str) for arg in value["argv"])):
                        raise ValueError("invalid hosted command primary references")
                    for name, digest in value["files"].items():
                        add(evidence_path(path.parent, name), digest)
                    for arg in value["argv"]:
                        if arg.startswith(("--verification-output=", "--junitxml=")):
                            target = Path(arg.partition("=")[2])
                            if not target.is_absolute() or not target.is_relative_to(work):
                                raise ValueError("hosted command primary output leaves the run")
                            if target.exists() or value.get("status") == "completed":
                                add(target)
                for name, digest in described(value):
                    if Path(name).is_absolute():
                        target = Path(name)
                        if not target.is_relative_to(work):
                            raise ValueError("hosted primary reference leaves the run")
                    else:
                        targets = set()
                        parent = path.parent
                        while parent.is_relative_to(work):
                            target = evidence_path(parent, name, must_exist=False)
                            if target.is_file():
                                targets.add(target)
                            if parent == work:
                                break
                            parent = parent.parent
                        if len(targets) != 1:
                            raise ValueError("missing or ambiguous hosted primary reference")
                        target = targets.pop()
                    add(target, digest)
                if isinstance(value.get("family_sha256"), str):
                    case = path.parent
                    if path.name.startswith("case-"):
                        case = case / (value["mode"] + "-" + value["fault"])
                    add(case / "affected/command/process-family.json", value["family_sha256"])
        return dict(sorted(files.items()))

    files = inventory()
    manifest = archive_evidence(work, output, roots={"openpine": host}, candidate=approved_sha,
        source_hash=expected_source_hash, run_id=run_id, retention_days=retention_days,
        max_bytes=max_bytes, primary_artifacts=files)
    if inventory() != files:
        raise ValueError("hosted primary inventory changed during archive readback")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "execute", "preflight", "archive"))
    parser.add_argument("--host", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--approved-sha")
    parser.add_argument("--public-approved", action="store_true")
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--archive-output", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--source-hash")
    parser.add_argument("--expected-candidate-manifest-sha256")
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--retention-days", type=int)
    args = parser.parse_args()
    if args.action == "execute":
        if args.spec is None:
            parser.error("execute requires --spec")
        return execute(args.spec, args.work)
    if args.host is None or args.approved_sha is None:
        parser.error("run requires --host and --approved-sha")
    if args.action == "archive":
        if any(value is None for value in (args.archive_output, args.run_id, args.source_hash,
                args.expected_candidate_manifest_sha256, args.max_bytes, args.retention_days)) or args.public_approved:
            parser.error("archive requires explicit private output, run/source/candidate identity, byte budget and retention")
        from openpine.verification.identity import canonical
        try:
            manifest = archive_primaries(args.host, args.work, args.archive_output,
                approved_sha=args.approved_sha, run_id=args.run_id, expected_source_hash=args.source_hash,
                expected_candidate_manifest_sha256=args.expected_candidate_manifest_sha256,
                max_bytes=args.max_bytes, retention_days=args.retention_days)
            print(canonical({"manifest_hash": manifest["content_hash"], "archive": manifest["archive"],
                "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"],
                "raw_primaries_durable": False, "full_qualification_accepted": False}).decode())
            return 0
        except (OSError, ValueError, KeyError, TypeError):
            print(canonical({"archived": False, "raw_primaries_durable": False,
                "full_qualification_accepted": False}).decode())
            return 1
    return prepare_and_run(args.host, args.work, args.approved_sha, args.public_approved, preflight=args.action == "preflight")


if __name__ == "__main__":
    raise SystemExit(main())
