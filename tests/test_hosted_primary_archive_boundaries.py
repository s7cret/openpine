"""Local archive integrity, using synthetic failure diagnostics only.

These fixtures neither execute nor accept a hosted/protected qualification.
"""

import pytest

from openpine.admission import candidate_manifest_hash
from openpine.verification.execution_ci import COMPONENTS, bundle_manifest
from openpine.verification.execution_identity import hash_file, source_snapshot
from openpine.verification.identity import read_json, seal, write_json
from openpine.verification.qualification_hosted import archive_primaries


def archive_inputs(tmp_path):
    roots = {}
    commits = {name: f"{i + 1:040x}" for i, name in enumerate(COMPONENTS)}
    for name in COMPONENTS:
        root = tmp_path / "sources" / name
        root.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "' + name + '"\nversion = "0.0.0"\n'
        )
        roots[name] = root
    host = roots["openpine"]
    (host / "candidates").mkdir()
    write_json(
        host / "candidates/stack-candidate-5.0.0-rc.6.template.json",
        {
            "components": {name: {"sha": sha} for name, sha in commits.items()},
        },
    )
    work = tmp_path / "run"
    private = work / "private"
    bundle = private / "prepare/bundle"
    bundle.mkdir(parents=True)
    source = source_snapshot(roots)
    bundle_manifest(
        bundle,
        source=source,
        environment=seal({"schema_id": "openpine.execution_environment.v1", "python": "3.13"}),
        source_pins={name: sha for name, sha in commits.items() if name != "openpine"},
        host_commit=commits["openpine"],
    )
    candidate = {
        "schema": "openpine.stack-candidate.v2",
        "stage": "wheel-bound",
        "not_a_release": True,
        "components": {name: {"sha": sha} for name, sha in commits.items()},
        "provenance": {"run_id": "unit-failed-run"},
    }
    candidate["manifest_hash"] = candidate_manifest_hash(candidate)
    write_json(private / "candidate.json", candidate)
    write_json(
        private / "driver-failure.json",
        {
            "ok": False,
            "raw_primaries_durable": False,
            "full_qualification_accepted": False,
            "error": "missing-input",
        },
    )
    kwargs = dict(
        approved_sha=commits["openpine"],
        run_id="unit-failed-run",
        expected_source_hash=source["content_hash"],
        expected_candidate_manifest_sha256=hash_file(private / "candidate.json"),
        max_bytes=10 * 1024 * 1024,
        retention_days=7,
    )
    return host, work, tmp_path / "archive", kwargs


@pytest.fixture
def archive_case(tmp_path):
    return archive_inputs(tmp_path)


def _archive(case):
    host, work, output, kwargs = case
    return archive_primaries(host, work, output, **kwargs)


def test_failed_diagnostics_archive_preserves_exact_raw_bytes_without_acceptance(archive_case):
    host, work, output, kwargs = archive_case
    raw = b"\xffincomplete raw diagnostic\n"
    (work / "private/broken.json").write_bytes(raw)
    result = _archive(archive_case)
    assert result
    assert (work / "private/broken.json").read_bytes() == raw
    terminal = read_json(work / "private/driver-failure.json")
    assert terminal["full_qualification_accepted"] is False
    assert terminal["raw_primaries_durable"] is False
    assert output.is_dir()


@pytest.mark.parametrize(
    "mutation",
    ["candidate", "source", "run", "sha", "budget", "retention", "budget-bool", "retention-bool"],
)
def test_independent_identity_and_budget_cannot_be_inferred_from_downloads(archive_case, mutation):
    host, work, output, kwargs = archive_case
    if mutation == "candidate":
        kwargs["expected_candidate_manifest_sha256"] = "sha256:" + "a" * 64
    elif mutation == "source":
        kwargs["expected_source_hash"] = "sha256:" + "b" * 64
    elif mutation == "run":
        kwargs["run_id"] = "other-run"
    elif mutation == "sha":
        kwargs["approved_sha"] = "f" * 40
    elif mutation == "budget":
        kwargs["max_bytes"] = 1
    elif mutation == "retention":
        kwargs["retention_days"] = 91
    elif mutation == "budget-bool":
        kwargs["max_bytes"] = True
    else:
        kwargs["retention_days"] = True
    with pytest.raises(ValueError):
        _archive(archive_case)


@pytest.mark.parametrize("field", ["raw_primaries_durable", "full_qualification_accepted"])
def test_terminal_cannot_self_declare_durability_or_qualification(archive_case, field):
    host, work, output, kwargs = archive_case
    path = work / "private/driver-failure.json"
    value = read_json(path)
    value[field] = True
    write_json(path, value)
    with pytest.raises(ValueError, match="cannot claim durable or full acceptance"):
        _archive(archive_case)


@pytest.mark.parametrize("location", ["root", "private", "prepare"])
def test_unknown_material_cannot_hide_in_archived_owner_locations(archive_case, location):
    host, work, output, kwargs = archive_case
    parent = {
        "root": work,
        "private": work / "private",
        "prepare": work / "private/prepare/bundle",
    }[location]
    (parent / "unexpected").mkdir()
    with pytest.raises(ValueError, match="unknown"):
        _archive(archive_case)


@pytest.mark.parametrize(
    "kind",
    [
        "missing-terminal",
        "invalid-terminal",
        "output-exists",
        "symlink-primary",
        "symlink-directory",
    ],
)
def test_archive_refuses_missing_terminal_and_unsafe_filesystem(archive_case, kind):
    host, work, output, kwargs = archive_case
    terminal = work / "private/driver-failure.json"
    if kind == "missing-terminal":
        terminal.unlink()
    elif kind == "invalid-terminal":
        write_json(terminal, [])
    elif kind == "output-exists":
        output.mkdir()
    elif kind == "symlink-primary":
        (work / "private/link").symlink_to(terminal)
    else:
        (work / "private/tests-int04A").symlink_to(work / "private", target_is_directory=True)
    with pytest.raises(ValueError):
        _archive(archive_case)


def test_transitive_reference_preserves_nonstandard_raw_material_and_rejects_tamper(archive_case):
    host, work, output, kwargs = archive_case
    material = work / "private/candidate-wheelhouse/raw.bin"
    material.parent.mkdir()
    material.write_bytes(b"independent raw bytes")
    diagnostic = work / "private/diagnostic.json"
    write_json(diagnostic, {"details": [{"path": str(material), "sha256": hash_file(material)}]})
    material.write_bytes(b"changed raw bytes")
    with pytest.raises(ValueError, match="checksum mismatch"):
        _archive(archive_case)


@pytest.mark.parametrize("reference", ["outside", "missing", "ambiguous", "invalid-checksum"])
def test_unbounded_or_ambiguous_primary_reference_cannot_enter_archive(archive_case, reference):
    host, work, output, kwargs = archive_case
    if reference == "outside":
        target = work.parent / "outside.bin"
        target.write_bytes(b"x")
        name = str(target)
    elif reference == "missing":
        target = work / "missing.bin"
        name = "missing.bin"
    elif reference == "ambiguous":
        target = work / "raw.bin"
        target.write_bytes(b"x")
        (work / "private/raw.bin").write_bytes(b"x")
        name = "raw.bin"
    else:
        target = work / "private/raw.bin"
        target.write_bytes(b"x")
        name = str(target)
    digest = (
        "not-a-hash"
        if reference == "invalid-checksum"
        else (hash_file(target) if target.exists() else "sha256:" + "c" * 64)
    )
    write_json(work / "private/diagnostic.json", {"reference": {"path": name, "sha256": digest}})
    with pytest.raises(ValueError):
        _archive(archive_case)


@pytest.mark.parametrize("primary", ["command.json", "execution.json"])
@pytest.mark.parametrize("payload", [b"{broken", b"[]", b'{"untrusted":true}'])
def test_reference_owners_fail_closed_even_when_raw_failure_diagnostics_are_retained(
    archive_case, primary, payload
):
    host, work, output, kwargs = archive_case
    folder = work / "private/tests-int04A"
    folder.mkdir()
    (folder / primary).write_bytes(payload)
    with pytest.raises(ValueError):
        _archive(archive_case)


def test_complete_fixture_archive_is_durable_bytes_only_not_kernel_qualification(archive_case):
    from openpine.verification.protected_qualification import MODES, FAULTS

    host, work, output, kwargs = archive_case
    private = work / "private"
    candidate_path = private / "candidate.json"
    commits = {name: row["sha"] for name, row in read_json(candidate_path)["components"].items()}
    for placement in ("A", "B"):
        write_json(
            private / ("binding-" + placement + ".json"),
            {
                "placement": placement,
                "source_commits": commits,
                "candidate_manifest": str(candidate_path),
                "candidate_manifest_sha256": kwargs["expected_candidate_manifest_sha256"],
            },
        )
        restored = work / ("placement-" + placement)
        restored.mkdir()
        write_json(
            restored / "restored.json",
            {"candidate_hash": kwargs["expected_source_hash"], "source_commits": commits},
        )
        # Explicit storage fixtures: no native observations, durations or acceptance.
        for label in ("int04", "int05-owner-contract", "protected"):
            for suffix in (".json", ".xml"):
                (private / (label + placement + suffix)).write_bytes(b"unit storage fixture")
            command = private / ("tests-" + label + placement)
            command.mkdir()
            write_json(
                command / "command.json",
                seal(
                    {
                        "schema_id": "openpine.execution_command.v1",
                        "argv": [],
                        "files": {},
                        "status": "failed",
                    }
                ),
            )
        command = private / ("matrix-command-" + placement)
        command.mkdir()
        write_json(
            command / "command.json",
            seal(
                {
                    "schema_id": "openpine.execution_command.v1",
                    "argv": [],
                    "files": {},
                    "status": "failed",
                }
            ),
        )
        faults = private / ("faults-" + placement)
        faults.mkdir()
        write_json(faults / "matrix.json", {"complete": False, "cases": []})
        for mode in MODES:
            for fault in FAULTS:
                folder = faults / (mode + "-" + fault)
                for relative in (
                    "automatic-result.json",
                    "forced-disposal.json",
                    "before.json",
                    "after.json",
                    "neighbour/native-result.json",
                ):
                    target = folder / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"fixture only")
                write_json(
                    faults / ("case-" + mode + "-" + fault + ".json"), {"fixture_only": True}
                )
                for role in ("affected", "neighbour"):
                    for relative in (
                        "allocated-unit.json",
                        "ready.json",
                        "controller.stdout",
                        "controller.stderr",
                        "command/stdout.log",
                        "command/stderr.log",
                        "command/process-family.json",
                        "command/input-0.bin",
                    ):
                        target = folder / role / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b"fixture only")
                    if role == "neighbour" or fault != "controller-sigkill":
                        write_json(
                            folder / role / "command/command.json",
                            seal(
                                {
                                    "schema_id": "openpine.execution_command.v1",
                                    "argv": [],
                                    "files": {},
                                    "status": "failed",
                                }
                            ),
                        )
    write_json(
        private / "outcome.json",
        {"ok": True, "raw_primaries_durable": False, "full_qualification_accepted": False},
    )
    assert _archive(archive_case)
    assert read_json(private / "outcome.json")["full_qualification_accepted"] is False
    assert read_json(private / "faults-A/matrix.json")["complete"] is False


def test_referenced_extra_bytes_are_retained_and_mutation_during_readback_is_rejected(
    archive_case, monkeypatch
):
    from openpine.verification import execution_evidence

    host, work, output, kwargs = archive_case
    material = work / "private/candidate-wheelhouse/raw.bin"
    material.parent.mkdir()
    material.write_bytes(b"immutable diagnostic input")
    write_json(
        work / "private/reference.json",
        {"refs": [{"path": str(material), "sha256": hash_file(material)}]},
    )
    original = execution_evidence.archive_evidence

    def mutate_after_archive(*args, **options):
        result = original(*args, **options)
        material.write_bytes(b"changed after archive")
        return result

    monkeypatch.setattr(execution_evidence, "archive_evidence", mutate_after_archive)
    with pytest.raises(ValueError):
        _archive(archive_case)


@pytest.mark.parametrize(
    "field", ["placement", "source_commits", "candidate_manifest", "candidate_manifest_sha256"]
)
def test_placement_cannot_substitute_independent_candidate_identity(archive_case, field):
    host, work, output, kwargs = archive_case
    candidate = work / "private/candidate.json"
    commits = {name: row["sha"] for name, row in read_json(candidate)["components"].items()}
    value = {
        "placement": "A",
        "source_commits": commits,
        "candidate_manifest": str(candidate),
        "candidate_manifest_sha256": kwargs["expected_candidate_manifest_sha256"],
    }
    value[field] = {} if field == "source_commits" else "foreign"
    write_json(work / "private/binding-A.json", value)
    with pytest.raises(ValueError, match="placement binding mismatch"):
        _archive(archive_case)


def test_command_output_reference_cannot_escape_owner_run(archive_case):
    host, work, output, kwargs = archive_case
    folder = work / "private/tests-int04A"
    folder.mkdir()
    write_json(
        folder / "command.json",
        seal(
            {
                "schema_id": "openpine.execution_command.v1",
                "argv": ["python", "--verification-output=" + str(work.parent / "outside.json")],
                "files": {},
                "status": "failed",
            }
        ),
    )
    with pytest.raises(ValueError, match="output leaves the run"):
        _archive(archive_case)
