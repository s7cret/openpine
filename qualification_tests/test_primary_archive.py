"""Real local archive transport and failed pytest bytes, never hosted acceptance."""
from __future__ import annotations

import gzip
import builtins
import json
from pathlib import Path
import shutil
import sys
import tarfile

import pytest

from openpine.verification import qualification_hosted as hosted
from openpine.verification import execution_evidence
from openpine.verification.execution_evidence import archive_evidence, verify_archive
from openpine.verification.execution_identity import hash_file, source_snapshot, write_once_json
from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json, seal
from qualification_tests.test_qualification_diagnostics import mixed_wheels
from scripts.finalize_stack_candidate import finalize_candidate

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = "a" * 40
RUN = "fixture"
CEILING = 1024 * 1024


def hosted_inputs(tmp_path: Path):
    host = tmp_path / "host"
    template = host / "candidates/stack-candidate-5.0.0-rc.6.template.json"
    template.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / template.relative_to(host), template)
    work = tmp_path / "work"
    private = work / "private"
    bundle = private / "prepare/bundle"
    bundle.mkdir(parents=True)
    wheels = private / "candidate-wheelhouse"
    source_candidate = mixed_wheels(host, bundle / "wheelhouse")
    hosted.candidate_wheelhouse(source_candidate, bundle / "wheelhouse", wheels)
    candidate = finalize_candidate(source_candidate, wheels)
    write_once_json(private / "candidate.json", candidate)
    commits = hosted.declared_commits(host, CANDIDATE)
    # Tiny actual source bytes let this transport test bind a real source hash
    # without building a product, restoring environments or claiming runtime proof.
    roots = {"openpine": host}
    for name in commits.keys() - {"openpine"}:
        root = tmp_path / "sources" / name
        root.mkdir(parents=True)
        (root / "source.txt").write_text(name)
        roots[name] = root
    source = source_snapshot(roots)
    write_once_json(bundle / "bundle.json", seal({"schema_id": "openpine.ci_prepared_environment.v1",
        "source": source, "source_pins": {n: c for n, c in commits.items() if n != "openpine"},
        "host_commit": CANDIDATE}))
    # Unrelated material/fixture symlinks are deliberately outside primary scope.
    (private / "test-namespace").mkdir()
    (private / "pytest-int04A").mkdir()
    (private / "pytest-int04A/current").symlink_to(private / "pytest-int04A", target_is_directory=True)
    (bundle / "wheelhouse/large-material.whl").write_bytes(b"material" * 200_000)
    write_once_json(private / "candidate-preflight.json", {"ok": True,
        "raw_primaries_durable": False, "full_qualification_accepted": False})
    kwargs = {"approved_sha": CANDIDATE, "run_id": RUN,
        "expected_source_hash": source["content_hash"],
        "expected_candidate_manifest_sha256": hash_file(private / "candidate.json"),
        "max_bytes": CEILING, "retention_days": 1}
    return host, work, kwargs


def archive(tmp_path: Path, host: Path, work: Path, kwargs: dict):
    return hosted.archive_primaries(host, work, tmp_path / "archive", **kwargs)


@pytest.mark.parametrize("success", [True, False])
def test_hosted_archive_preserves_real_pytest_owner_junit_command_and_failure(tmp_path, success):
    host, work, kwargs = hosted_inputs(tmp_path)
    private = work / "private"
    test = private / "test-namespace/test_primary.py"
    test.write_text("def test_primary():\n    assert " + str(success) + "\n")
    owner, junit = private / "int04A.json", private / "int04A.xml"
    argv = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-p", "openpine.verification.pytest_gate", "--verification-output=" + str(owner),
        "--verification-suite=int04", "--junitxml=" + str(junit), str(test)]
    env = {**hosted.clean_environment(), "PYTHONPATH": str(ROOT)}
    result = run_logged(argv, cwd=work, output=private / "tests-int04A", env=env, timeout=30)
    assert result["ok"] is success
    assert read_json(owner)["ok"] is success
    if not success:
        write_once_json(private / "driver-failure.json", {"stage": "int04-A", "error": "command-failed",
            "traceback": "private-original-failure"})
    fault = private / "faults-A/interactive-timeout"
    (fault / "neighbour").mkdir(parents=True)
    (fault / "neighbour/native-result.json").write_text('{"ok":true}')
    (fault / "affected").mkdir()
    (fault / "affected/.guardian-actual.json").write_bytes(b'{"cleanup_verified":true}')
    originals = {p.relative_to(work).as_posix(): p.read_bytes()
                 for p in work.rglob("*") if p.is_file() and not p.is_symlink()}
    manifest = archive(tmp_path, host, work, kwargs)
    for required in ("private/int04A.json", "private/int04A.xml", "private/tests-int04A/stdout.log",
                     "private/tests-int04A/stderr.log", "private/tests-int04A/command.json",
                     "private/tests-int04A/process-family.json",
                     "private/faults-A/interactive-timeout/neighbour/native-result.json",
                     "private/faults-A/interactive-timeout/affected/.guardian-actual.json"):
        assert required in manifest["files"]
    assert not any("wheelhouse/" in name or "test-namespace/" in name or "pytest-int04A/" in name
                   for name in manifest["files"])
    assert manifest["full_product_accepted"] is False
    assert (tmp_path / "archive").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "archive/evidence.tar.gz").stat().st_mode & 0o777 == 0o600
    download = tmp_path / "portable-download"
    shutil.copytree(tmp_path / "archive", download)
    checked = verify_archive(download / "evidence.tar.gz", read_json(download / "manifest.json"),
        expected_manifest_hash=manifest["content_hash"], expected_candidate=CANDIDATE,
        expected_run_id=RUN, expected_source_hash=kwargs["expected_source_hash"])
    assert checked["verified"] is True and checked["full_product_accepted"] is False
    with tarfile.open(download / "evidence.tar.gz") as stream:
        for member in stream:
            with stream.extractfile(member) as content:
                assert content.read() == originals[member.name]
    assert {p.relative_to(work).as_posix(): p.read_bytes() for p in work.rglob("*")
            if p.is_file() and not p.is_symlink()} == originals
    (download / "evidence.tar.gz").write_bytes((download / "evidence.tar.gz").read_bytes()[:-8])
    with pytest.raises(ValueError, match="checksum or size mismatch"):
        verify_archive(download / "evidence.tar.gz", manifest,
            expected_manifest_hash=manifest["content_hash"], expected_candidate=CANDIDATE,
            expected_run_id=RUN, expected_source_hash=kwargs["expected_source_hash"])
    if success:
        junit.unlink()
        with pytest.raises(ValueError, match="missing evidence file"):
            hosted.archive_primaries(host, work, tmp_path / "missing-junit-archive", **kwargs)
        assert not (tmp_path / "missing-junit-archive").exists()


@pytest.mark.parametrize("identity", ["approved_sha", "run_id", "expected_source_hash",
                                     "expected_candidate_manifest_sha256"])
def test_hosted_archive_rejects_foreign_independent_identity_before_write(tmp_path, identity):
    host, work, kwargs = hosted_inputs(tmp_path)
    kwargs[identity] = "b" * 40 if identity == "approved_sha" else "other-run" if identity == "run_id" else "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="identity mismatch"):
        archive(tmp_path, host, work, kwargs)
    assert not (tmp_path / "archive").exists()
    assert (work / "private/candidate.json").is_file()


@pytest.mark.parametrize("damage", ["missing", "tampered", "symlink", "traversal", "budget"])
def test_referenced_primary_in_fixture_tree_cannot_be_omitted(tmp_path, damage):
    host, work, kwargs = hosted_inputs(tmp_path)
    fixture = work / "private/pytest-int04A/required.bin"
    fixture.write_bytes(b"referenced-original-bytes")
    digest = hash_file(fixture)
    write_once_json(work / "private/owner.json", {"primary": {"path": "pytest-int04A/required.bin",
        "sha256": digest}})
    if damage == "missing":
        fixture.unlink()
    elif damage == "tampered":
        fixture.write_bytes(b"changed")
    elif damage == "symlink":
        fixture.unlink()
        fixture.symlink_to(work / "private/candidate.json")
    elif damage == "traversal":
        (work / "private/owner.json").unlink()
        write_once_json(work / "private/owner.json", {"primary": {"path": "../escape", "sha256": digest}})
    else:
        kwargs["max_bytes"] = 32
    with pytest.raises(ValueError):
        archive(tmp_path, host, work, kwargs)
    assert not (tmp_path / "archive").exists()
    assert (work / "private/owner.json").is_file()


def test_referenced_fixture_primary_and_partial_failed_command_are_preserved(tmp_path):
    host, work, kwargs = hosted_inputs(tmp_path)
    fixture = work / "private/pytest-int04A/required.bin"
    fixture.write_bytes(b"referenced-original-bytes")
    write_once_json(work / "private/owner.json", {"primary": {"path": "pytest-int04A/required.bin", "sha256": hash_file(fixture)}})
    command = work / "private/matrix-command-A"
    command.mkdir()
    (command / "stderr.log").write_bytes(b"killed-before-receipt\x00\xff")
    manifest = archive(tmp_path, host, work, kwargs)
    assert "private/pytest-int04A/required.bin" in manifest["files"]
    assert "private/matrix-command-A/stderr.log" in manifest["files"]
    assert not (command / "command.json").exists()


@pytest.mark.parametrize("owner", ["command.json", "execution.json"])
@pytest.mark.parametrize("damage", ["oversized", "nonobject", "invalid-schema"])
def test_mandatory_reference_owner_cannot_hide_a_fixture_primary(tmp_path, monkeypatch, owner, damage):
    host, work, kwargs = hosted_inputs(tmp_path)
    fixture = work / "private/pytest-int04A/required.bin"
    fixture.write_bytes(b"required-original-primary")
    output = work / "private/matrix-command-A"
    receipt = run_logged([sys.executable, "-B", "-c", "pass"], cwd=work,
        output=output, env=hosted.clean_environment(), timeout=30,
        inputs={"required": {"path": str(fixture), "sha256": hash_file(fixture)}})
    path = output / owner
    if owner == "execution.json":
        receipt = {"task": "fixture@py", "shard": "s000", "attempt_id": "a001", "run_id": RUN,
            "status": "completed", "returncode": 0, "argv": receipt["argv"], "cwd": str(work),
            "artifacts": {"required": {"path": str(fixture), "sha256": hash_file(fixture)}}}
        write_once_json(path, receipt)
    if damage == "oversized":
        # Keep the production branch small while exercising the real bounded
        # pipeline. The appended whitespace preserves the valid sealed object.
        monkeypatch.setattr(execution_evidence, "MANIFEST_LIMIT", 8192)
        with path.open("ab") as stream:
            stream.write(b" " * 8193)
    elif damage == "nonobject":
        path.write_text(json.dumps([{"path": str(fixture), "sha256": hash_file(fixture)}]))
    else:
        receipt["schema_id"] = "foreign.reference-owner.v1"
        if owner == "command.json":
            receipt = seal({k: v for k, v in receipt.items() if k != "content_hash"})
        path.write_text(json.dumps(receipt))
    originals = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="hosted primary reference owner"):
        archive(tmp_path, host, work, kwargs)
    assert not (tmp_path / "archive").exists()
    assert fixture.read_bytes() == b"required-original-primary"
    assert {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()} == originals


@pytest.mark.parametrize("raw", [b"{incomplete failure diagnostic", b"[" + b" " * 8193 + b"]"])
def test_incomplete_other_raw_diagnostics_remain_archived_verbatim(tmp_path, monkeypatch, raw):
    host, work, kwargs = hosted_inputs(tmp_path)
    monkeypatch.setattr(execution_evidence, "MANIFEST_LIMIT", 8192)
    diagnostic = work / "private/partial-diagnostic.json"
    diagnostic.write_bytes(raw)
    manifest = archive(tmp_path, host, work, kwargs)
    assert manifest["files"]["private/partial-diagnostic.json"]["sha256"] == hash_file(diagnostic)
    with tarfile.open(tmp_path / "archive/evidence.tar.gz") as stream:
        with stream.extractfile("private/partial-diagnostic.json") as content:
            assert content.read() == raw


def successful_transport_inputs(tmp_path):
    """Exact success file layout fixture; no worker or qualification proof."""
    host, work, kwargs = hosted_inputs(tmp_path)
    private = work / "private"
    candidate = private / "candidate.json"
    commits = hosted.declared_commits(host, CANDIDATE)

    def primary(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_once_json(path, value)

    def command(folder, status="completed", binding=None):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "stdout.log").write_bytes(b"original stdout")
        (folder / "stderr.log").write_bytes(b"original stderr")
        primary(folder / "process-family.json", {"fixture": "archive transport only"})
        if binding is not None:
            (folder / "input-0.bin").write_bytes(binding.read_bytes())
        files = {p.name: hash_file(p) for p in folder.iterdir() if p.is_file()}
        primary(folder / "command.json", seal({"schema_id": "openpine.execution_command.v1",
            "files": files, "argv": [sys.executable, "-c", "pass"], "status": status}))

    for placement in ("A", "B"):
        binding = private / ("binding-" + placement + ".json")
        primary(binding, {"placement": placement, "source_commits": commits,
            "candidate_manifest": str(candidate), "candidate_manifest_sha256": kwargs["expected_candidate_manifest_sha256"]})
        primary(work / ("placement-" + placement) / "restored.json", {
            "candidate_hash": kwargs["expected_source_hash"], "source_commits": commits})
        for label in ("int04", "int05-owner-contract", "protected"):
            primary(private / (label + placement + ".json"), {"fixture": "transport only"})
            (private / (label + placement + ".xml")).write_bytes(b"<testsuites/>")
            command(private / ("tests-" + label + placement))
        command(private / ("matrix-command-" + placement))
        faults = private / ("faults-" + placement)
        primary(faults / "matrix.json", {"fixture": "transport only"})
        for mode in hosted.MODES:
            for fault in hosted.FAULTS:
                case = faults / (mode + "-" + fault)
                primary(faults / ("case-" + mode + "-" + fault + ".json"), {"fixture": "transport only"})
                for name in ("automatic-result.json", "forced-disposal.json", "before.json", "after.json"):
                    primary(case / name, {"fixture": "transport only"})
                for role in ("affected", "neighbour"):
                    folder = case / role
                    for name in ("allocated-unit.json", "ready.json"):
                        primary(folder / name, {"fixture": "transport only"})
                    (folder / "controller.stdout").write_bytes(b"original controller stdout")
                    (folder / "controller.stderr").write_bytes(b"original controller stderr")
                    command(folder / "command", "timeout" if role == "affected" and fault == "timeout"
                            else "failed" if role == "affected" else "completed", binding)
                    if role == "affected" and fault == "controller-sigkill":
                        (folder / "command/command.json").unlink()
                primary(case / "neighbour/native-result.json", {"ok": True, "bars": 6,
                    "intent": ["search", 2, 3], "trade": [103, 3]})
    primary(private / "outcome.json", {"ok": True,
        "raw_primaries_durable": False, "full_qualification_accepted": False})
    return host, work, kwargs


@pytest.mark.parametrize("fault,primary", [
    ("timeout", "affected/command/command.json"), ("sigint", "affected/command/command.json"),
    ("controller-sigkill", "affected/command/stdout.log"), ("timeout", "affected/controller.stderr"),
    ("timeout", "neighbour/command/command.json"), ("timeout", "neighbour/command/process-family.json"),
    ("timeout", "neighbour/controller.stdout"), ("controller-sigkill", "affected/command/input-0.bin"),
])
def test_success_archive_requires_reached_fault_command_and_controller_primaries(tmp_path, fault, primary):
    host, work, kwargs = successful_transport_inputs(tmp_path)
    removed = work / "private/faults-A" / ("interactive-" + fault) / primary
    removed.unlink()
    with pytest.raises(ValueError, match="missing evidence file"):
        archive(tmp_path, host, work, kwargs)
    assert not (tmp_path / "archive").exists()
    assert (work / "private/outcome.json").is_file()


def test_complete_success_layout_archives_legitimate_sigkill_receipt_absence(tmp_path):
    host, work, kwargs = successful_transport_inputs(tmp_path)
    manifest = archive(tmp_path, host, work, kwargs)
    assert manifest["full_product_accepted"] is False
    for placement in ("A", "B"):
        for mode in hosted.MODES:
            case = "private/faults-" + placement + "/" + mode + "-controller-sigkill"
            assert case + "/affected/command/command.json" not in manifest["files"]
            assert case + "/neighbour/command/command.json" in manifest["files"]


def test_archive_uses_existing_packaged_hash_owner_without_script_namespace(tmp_path, monkeypatch):
    host, work, kwargs = hosted_inputs(tmp_path)
    original_import = builtins.__import__

    def installed_import(name, *args, **options):
        if name == "scripts" or name.startswith("scripts."):
            raise ModuleNotFoundError("scripts are absent from the product wheel")
        return original_import(name, *args, **options)

    # Source-test namespace helpers may expose scripts; archive must work with
    # the wheel's actual packaged capabilities even under those test helpers.
    monkeypatch.setattr(builtins, "__import__", installed_import)
    manifest = archive(tmp_path, host, work, kwargs)
    assert manifest["full_product_accepted"] is False


@pytest.mark.parametrize("damage", ["missing-preparation", "missing-candidate", "unknown-primary-tree", "successful-missing-junit"])
def test_hosted_archive_rejects_missing_binding_or_incomplete_success(tmp_path, damage):
    host, work, kwargs = hosted_inputs(tmp_path)
    if damage == "missing-preparation":
        (work / "private/prepare/bundle/bundle.json").unlink()
    elif damage == "missing-candidate":
        (work / "private/candidate.json").unlink()
    elif damage == "unknown-primary-tree":
        (work / "private/unknown-owner").mkdir()
    else:
        write_once_json(work / "private/outcome.json", {"ok": True,
            "raw_primaries_durable": False, "full_qualification_accepted": False})
    with pytest.raises(ValueError):
        archive(tmp_path, host, work, kwargs)
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("damage", ["hidden-tail", "zero-tail", "pax-bomb"])
def test_archive_readback_rejects_swallowed_tail_and_expansion_bomb(tmp_path, damage):
    source = tmp_path / "source"
    source.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "primary.log").write_bytes(b"x")
    manifest = archive_evidence(evidence, tmp_path / "archive", roots={"source": source},
        candidate=CANDIDATE, run_id=RUN, retention_days=1, max_bytes=CEILING)
    path = tmp_path / "archive/evidence.tar.gz"
    # A valid member, tar terminator, and malicious bytes all fit in the old
    # tar stream's read-ahead buffer for the nonzero-tail regression.
    original = gzip.decompress(path.read_bytes())
    if damage == "pax-bomb":
        pax = tarfile.TarInfo("unbounded-pax")
        pax.type, pax.size = tarfile.XHDTYPE, 2_000_000
        body = pax.tobuf() + b"\0" * pax.size
    else:
        body = original[:2048] + (b"hidden-tail" if damage == "hidden-tail" else b"\0" * 2_000_000)
    path.write_bytes(gzip.compress(body, mtime=0))
    manifest["archive"].update(sha256=hash_file(path), size=path.stat().st_size)
    manifest = seal({k: v for k, v in manifest.items() if k != "content_hash"})
    with pytest.raises(ValueError, match="trailing payload|framing exceeds budget"):
        verify_archive(path, manifest, expected_manifest_hash=manifest["content_hash"],
            expected_candidate=CANDIDATE, expected_run_id=RUN)


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, *range(1024, 20 * 512, 512)])
@pytest.mark.parametrize("name", ["primary.log", "long-" + "π" * 60 + ".log"])
def test_valid_archive_reads_back_across_every_tar_record_alignment(tmp_path, size, name):
    source = tmp_path / "source"
    source.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    primary = evidence / name
    primary.write_bytes(b"x" * size)
    manifest = archive_evidence(evidence, tmp_path / "archive", roots={"source": source},
        candidate=CANDIDATE, run_id=RUN, retention_days=1, max_bytes=CEILING)
    archive_path = tmp_path / "archive/evidence.tar.gz"
    result = verify_archive(archive_path, manifest, expected_manifest_hash=manifest["content_hash"],
        expected_candidate=CANDIDATE, expected_run_id=RUN)
    assert result["verified"] is True and result["total_bytes"] == size
    with tarfile.open(archive_path) as stream:
        with stream.extractfile(name) as content:
            assert content.read() == primary.read_bytes()


def test_archive_cli_stays_closed_on_failure_and_never_marks_durable(tmp_path, monkeypatch, capsys):
    host, work, kwargs = hosted_inputs(tmp_path)
    argv = ["archive", "--host", str(host), "--work", str(work), "--archive-output", str(tmp_path / "archive"),
        "--approved-sha", CANDIDATE, "--run-id", RUN, "--source-hash", kwargs["expected_source_hash"],
        "--expected-candidate-manifest-sha256", kwargs["expected_candidate_manifest_sha256"],
        "--max-bytes", str(CEILING), "--retention-days", "1"]
    monkeypatch.setattr(sys, "argv", ["qualification_hosted", *argv])
    assert hosted.main() == 0
    value = json.loads(capsys.readouterr().out)
    assert value["raw_primaries_durable"] is False and value["full_qualification_accepted"] is False
    assert not (work / "public").exists()
    assert hosted.main() == 1
    assert json.loads(capsys.readouterr().out) == {"archived": False,
        "raw_primaries_durable": False, "full_qualification_accepted": False}
