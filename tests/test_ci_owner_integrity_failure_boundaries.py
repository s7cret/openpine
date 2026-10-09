"""Real raw command and archive integrity failures cannot grant CI acceptance."""

from types import SimpleNamespace
import sys
import pytest
from openpine.verification import execution_ci as ci
from openpine.verification import stabilization_evidence as ev
from openpine.verification import execution_process as process
from openpine.verification.execution_identity import clean_environment, hash_file, source_snapshot
from openpine.verification.identity import read_json, write_json, seal
from tests.test_ci_control_plane_real import _source_roots


@pytest.mark.parametrize("version", ["py312", "py313t", "foreign"])
def test_builtin_owner_refuses_unsupported_interpreter_before_copy(tmp_path, version):
    with pytest.raises(ValueError):
        ci.retain_builtin_owner_evidence(
            tmp_path / "merged",
            tmp_path / "suites",
            tmp_path / "evidence",
            version,
            tmp_path / "pins",
        )
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    "mutation", ["pins", "existing-pins", "traversal", "absolute", "checksum", "conflict"]
)
def test_builtin_join_refuses_corrupted_or_foreign_primary(tmp_path, mutation):
    merged = tmp_path / "merged"
    merged.mkdir()
    suites = tmp_path / "suites"
    (suites / "py313").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    pins = {name: "a" * 40 for name in ci.COMPONENTS if name != "openpine"}
    write_json(suites / "py313/source-pins.json", pins)
    declared = tmp_path / "pins.json"
    write_json(declared, {**pins, "foreign": "b" * 40} if mutation == "pins" else pins)
    raw = merged / "observation.json"
    raw.write_bytes(b"actual bytes")
    name = "observations/owner.json"
    if mutation == "traversal":
        name = "../outside.json"
    elif mutation == "absolute":
        name = "/outside.json"
    digest = hash_file(raw) if mutation != "checksum" else "sha256:" + "0" * 64
    write_json(
        merged / "run.json",
        {
            "attempts": [
                {
                    "task": "optimizer@py313",
                    "artifacts": {"owner:" + name: {"path": "observation.json", "sha256": digest}},
                }
            ]
        },
    )
    if mutation in {"existing-pins", "conflict"}:
        evidence.mkdir()
        write_json(evidence / "source-pins.json", {} if mutation == "existing-pins" else pins)
        if mutation == "conflict":
            (evidence / "observations").mkdir()
            (evidence / "observations/owner.json").write_bytes(b"other bytes")
    with pytest.raises(ValueError):
        ci.retain_builtin_owner_evidence(merged, suites, evidence, "py313", declared)
    assert not (tmp_path / "outside.json").exists()


def test_builtin_join_is_idempotent_only_for_identical_raw_bytes(tmp_path):
    merged = tmp_path / "merged"
    merged.mkdir()
    suites = tmp_path / "suites"
    (suites / "py313").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    pins = {"optimizer": "a" * 40}
    write_json(suites / "py313/source-pins.json", pins)
    declared = tmp_path / "pins.json"
    write_json(declared, pins)
    raw = merged / "raw.bin"
    raw.write_bytes(b"\xffraw\x00")
    write_json(
        merged / "run.json",
        {
            "attempts": [
                {"task": "optimizer@py312", "artifacts": {}},
                {
                    "task": "optimizer@py313",
                    "artifacts": {
                        "not-owner": {},
                        "owner:observations/owner.bin": {
                            "path": "raw.bin",
                            "sha256": hash_file(raw),
                        },
                    },
                },
            ]
        },
    )
    for _ in range(2):
        ci.retain_builtin_owner_evidence(merged, suites, evidence, "py313", declared)
    assert (evidence / "observations/owner.bin").read_bytes() == raw.read_bytes()


@pytest.mark.parametrize(
    "mutation", ["returncode", "argv", "cwd", "stderr", "family", "stdout-value", "input-missing"]
)
def test_command_verifier_rereads_real_primaries_and_frozen_identity(tmp_path, mutation):
    folder = tmp_path / "native"
    argv = [sys.executable, "-c", "print(81)"]
    env = clean_environment({}, tmp_path / "private")
    receipt = process.run_logged(argv, cwd=tmp_path, output=folder, env=env, timeout=15)
    assert receipt["ok"]
    expected = {"argv": argv, "cwd": str(tmp_path)}
    if mutation == "argv":
        expected["argv"] = [sys.executable, "-c", "print(82)"]
    elif mutation == "cwd":
        expected["cwd"] = str(tmp_path / "foreign")
    elif mutation == "stderr":
        (folder / "stderr.log").write_bytes(b"altered")
    elif mutation == "family":
        (folder / "process-family.json").unlink()
    elif mutation == "stdout-value":
        expected["expected_stdout"] = 82
    elif mutation == "input-missing":
        expected["inputs"] = {"fixture": {"path": "unused", "sha256": "sha256:" + "a" * 64}}
    else:
        value = {k: v for k, v in receipt.items() if k != "content_hash"}
        value["returncode"] = True
        write_json(folder / "command.json", seal(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        ev.verify_command(folder, expected, expected_stdout=expected.get("expected_stdout"))


@pytest.mark.parametrize("mutation", ["checksum", "source", "extra", "symlink"])
def test_prepared_bundle_requires_complete_unmodified_inventory(tmp_path, mutation):
    from openpine.verification.execution_identity import environment_snapshot

    roots = _source_roots(tmp_path)
    source = source_snapshot(roots)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    file = bundle / "raw.bin"
    file.write_bytes(b"archive bytes")
    ci.bundle_manifest(
        bundle,
        source=source,
        environment=environment_snapshot(),
        source_pins={name: "a" * 40 for name in ci.COMPONENTS if name != "openpine"},
        host_commit="a" * 40,
    )
    if mutation == "checksum":
        file.write_bytes(b"corrupt")
    elif mutation == "extra":
        (bundle / "unexpected").write_bytes(b"extra")
    elif mutation == "symlink":
        (bundle / "linked").symlink_to(file)
    with pytest.raises(ValueError):
        ci.verify_bundle(
            bundle, expected_source_hash="sha256:" + "0" * 64 if mutation == "source" else None
        )


@pytest.mark.parametrize("action", ["plan", "task", "restore", "foundation", "stabilization"])
def test_ci_dispatch_refuses_plan_without_exact_producer_commits(tmp_path, action):
    path = tmp_path / "plan.json"
    write_json(path, {"source": {}})
    args = SimpleNamespace(ci_action=action, plan=path)
    if action == "plan":
        args.bundle = []
        args.work = tmp_path / "work"
        args.owner_locations = None
        args.output = tmp_path / "out"
    with pytest.raises((ValueError, KeyError)):
        ci.run_ci_command(args)
    assert not (tmp_path / "out").exists()


def test_failed_commands_keep_raw_non_utf8_logs_and_never_emit_success(tmp_path):
    commands = ci.Commands(tmp_path / "commands")
    with pytest.raises(RuntimeError) as caught:
        commands.run(
            [
                sys.executable,
                "-c",
                "import os;os.write(1,b'\\xffstdout');os.write(2,b'\\xfestderr');raise SystemExit(11)",
            ],
            cwd=tmp_path,
            timeout=15,
        )
    log = tmp_path / "commands/commands/0000"
    assert (log / "stdout.log").read_bytes() == b"\xffstdout" and (
        log / "stderr.log"
    ).read_bytes() == b"\xfestderr"
    assert read_json(log / "command.json")["returncode"] == 11
    assert "raw receipt:" in str(caught.value) and commands.index == 1
