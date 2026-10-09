"""Wheel selection and publication integrity; fixtures are not candidate packages."""

from types import SimpleNamespace
import json
import sys
import zipfile
import pytest
from openpine.verification import qualification_hosted as h
from openpine.verification import qualification_public as p
from openpine.verification.identity import read_json, write_json
from tests.test_hosted_primary_archive_boundaries import archive_inputs
from tests.test_qualification_projection_boundaries import ALLOWLIST, projection, successful_rows


@pytest.fixture
def wheels(tmp_path):
    folder = tmp_path / "wheelhouse"
    folder.mkdir()
    source = {"components": {name: {"version": "1.0.0"} for name in p.COMPONENTS}}
    for name in p.COMPONENTS:
        normalized = name.replace("-", "_")
        with zipfile.ZipFile(folder / (normalized + "-1.0.0-py3-none-any.whl"), "w") as z:
            z.writestr(
                normalized + "-1.0.0.dist-info/METADATA",
                "Metadata-Version: 2.3\nName: " + name + "\nVersion: 1.0.0\n",
            )
    return source, folder, tmp_path / "selected"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "symlink",
        "directory",
        "invalid-filename",
        "duplicate",
        "metadata-name",
        "metadata-version",
        "metadata-missing",
        "zip-broken",
        "unknown-component",
    ],
)
def test_incomplete_or_ambiguous_candidate_set_publishes_no_wheels(wheels, tmp_path, mutation):
    source, folder, output = wheels
    first = sorted(folder.glob("*.whl"))[0]
    if mutation == "missing":
        first.unlink()
    elif mutation == "symlink":
        target = tmp_path / "outside.whl"
        first.rename(target)
        first.symlink_to(target)
    elif mutation == "directory":
        first.unlink()
        first.mkdir()
    elif mutation == "invalid-filename":
        (folder / "invalid.whl").write_bytes(b"x")
    elif mutation == "duplicate":
        (folder / first.name.replace("-py3-", "-py2-")).write_bytes(first.read_bytes())
    elif mutation == "zip-broken":
        first.write_bytes(b"bad zip")
    elif mutation == "unknown-component":
        source["components"].pop("openpine")
    else:
        first.unlink()
        with zipfile.ZipFile(first, "w") as z:
            if mutation != "metadata-missing":
                z.writestr(
                    "fixture.dist-info/METADATA",
                    "Metadata-Version: 2.3\nName: "
                    + ("foreign" if mutation == "metadata-name" else first.name.split("-")[0])
                    + "\nVersion: "
                    + ("2.0.0" if mutation == "metadata-version" else "1.0.0")
                    + "\n",
                )
    # Corrupt ZIPs propagate the archive error to the outer closed driver boundary.
    with pytest.raises((h.QualificationFailure, zipfile.BadZipFile)):
        h.candidate_wheelhouse(source, folder, output)
    assert not output.exists()


def test_exact_selection_preserves_bytes_and_ignores_third_party_vendor_metadata(wheels):
    source, folder, output = wheels
    with zipfile.ZipFile(folder / "third_party-1.0.0-py3-none-any.whl", "w") as z:
        z.writestr("vendored/METADATA", "untrusted vendor metadata")
    assert h.candidate_wheelhouse(source, folder, output) == output
    assert len(list(output.iterdir())) == 8
    for file in output.iterdir():
        assert file.read_bytes() == (folder / file.name).read_bytes()


@pytest.mark.parametrize("mutation", ["folder-missing", "folder-symlink", "output-exists"])
def test_wheel_selection_does_not_replace_existing_material(wheels, tmp_path, mutation):
    source, folder, output = wheels
    if mutation == "folder-missing":
        folder = tmp_path / "absent"
    elif mutation == "folder-symlink":
        link = tmp_path / "link"
        link.symlink_to(folder)
        folder = link
    else:
        output.mkdir()
        (output / "keep").write_bytes(b"unchanged")
    with pytest.raises((h.QualificationFailure, FileExistsError)):
        h.candidate_wheelhouse(source, folder, output)
    if mutation == "output-exists":
        assert (output / "keep").read_bytes() == b"unchanged"


@pytest.mark.parametrize("status,code", [("failed", "command-failed")])
def test_hosted_command_failure_preserves_native_raw_logs(tmp_path, status, code):
    # Invoke real supervision and compare retained stdout/stderr/exit status.
    script = (
        "import sys; print('native stdout'); print('native stderr',file=sys.stderr); sys.exit(7)"
    )
    with pytest.raises(h.QualificationFailure) as caught:
        h.call([sys.executable, "-c", script], tmp_path, tmp_path / "command")
    assert caught.value.code == code
    raw = read_json(tmp_path / "command/command.json")
    assert raw["returncode"] == 7 and raw["ok"] is False
    assert (tmp_path / "command/stdout.log").read_text() == "native stdout\n"
    assert (tmp_path / "command/stderr.log").read_text() == "native stderr\n"


def test_disk_reserve_refuses_launch_without_emitting_success(tmp_path, monkeypatch):
    monkeypatch.setattr(h.shutil, "disk_usage", lambda *a: SimpleNamespace(free=2999999999))
    from openpine.verification import execution_process

    monkeypatch.setattr(
        execution_process, "run_logged", lambda *a, **k: pytest.fail("disk guard launched process")
    )
    with pytest.raises(h.QualificationFailure) as caught:
        h.call([sys.executable, "-c", "pass"], tmp_path, tmp_path / "command")
    assert caught.value.code == "disk-reserve" and not (tmp_path / "command").exists()


@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled"])
def test_workflow_failure_downgrades_complete_metadata_without_claiming_durability(
    tmp_path, outcome
):
    host, work, output, identity = archive_inputs(tmp_path)
    allow = host / "verification/protected-qualification-public-allowlist.json"
    allow.parent.mkdir()
    allow.write_bytes(ALLOWLIST.read_bytes())
    sha = identity["approved_sha"]
    rows = successful_rows()
    commits = {name: sha for name in p.COMPONENTS}
    value = p.project(sha, commits, rows, allow, owner_checks_passed=True)
    folder = tmp_path / "public"
    p.write_projection(folder, value)
    result = p.ensure_projection(host, folder, sha, outcome)
    assert not result["ok"] and result["error"] == "incomplete"
    assert not result["full_qualification_accepted"] and not result["raw_primaries_durable"]


@pytest.mark.parametrize("mutation", ["missing", "foreign-sha", "corrupt", "bad-template"])
def test_workflow_missing_or_invalid_public_output_becomes_closed_failure(tmp_path, mutation):
    host, work, output, identity = archive_inputs(tmp_path)
    allow = host / "verification/protected-qualification-public-allowlist.json"
    allow.parent.mkdir()
    allow.write_bytes(ALLOWLIST.read_bytes())
    folder = tmp_path / "public"
    sha = identity["approved_sha"]
    if mutation == "foreign-sha":
        p.write_projection(folder, projection())
    if mutation == "corrupt":
        folder.mkdir()
        (folder / "projection.json").write_text("private secret")
    if mutation == "bad-template":
        (host / "candidates/stack-candidate-5.0.0-rc.6.template.json").write_text("{}")
    value = p.ensure_projection(host, folder, sha, "failure")
    assert not value["ok"] and value["error"] in {"missing-output", "projection-invalid"}
    assert "private secret" not in (folder / "projection.json").read_text()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_id", "foreign"),
        ("retention_days", 2),
        ("max_complete_upload_bytes", 1),
        ("files", ["private.json"]),
        ("unknown_identity_on_failure", {}),
    ],
)
def test_public_allowlist_cannot_be_weakened(tmp_path, field, value):
    policy = json.loads(ALLOWLIST.read_text())
    policy[field] = value
    file = tmp_path / "allowlist.json"
    write_json(file, policy)
    with pytest.raises(ValueError, match="closed contract"):
        p.project("a" * 40, {name: "a" * 40 for name in p.COMPONENTS}, [], file)


@pytest.mark.parametrize("value", [None, {}, 1, "private"])
def test_public_case_list_requires_objects(value):
    with pytest.raises(ValueError):
        p.project("a" * 40, {name: "a" * 40 for name in p.COMPONENTS}, value, ALLOWLIST)


@pytest.mark.parametrize("value", [None, 1, "yes"])
def test_public_owner_outcome_requires_boolean(value):
    with pytest.raises(ValueError):
        projection(owner_checks_passed=value)


@pytest.mark.parametrize(
    "mutation",
    ["complete-failure", "wrong-fault-receipt", "wrong-fault-exit", "unknown-diagnostic"],
)
def test_failed_case_cannot_contradict_fault_and_completion(mutation):
    row = successful_rows()[0]
    row.update(
        ok=False,
        automatic_cleanup=False,
        neighbour_survived=False,
        neighbour_completed=False,
        case_stage="family-receipt",
        case_error="family-controller-mismatch",
        neighbour_completion_state="not-reached",
    )
    if mutation == "complete-failure":
        row.update(
            case_stage="complete",
            case_error="none",
            neighbour_completed=True,
            neighbour_completion_state="observed-true",
        )
    elif mutation == "wrong-fault-receipt":
        row.update(fault="timeout", case_error="receipt-unexpected")
    elif mutation == "wrong-fault-exit":
        row.update(fault="timeout", case_error="command-exit-mismatch")
    else:
        row["case_error"] = "private secret"
    with pytest.raises(ValueError):
        projection([row])


def test_closed_cli_diagnostic_writes_only_public_summary(tmp_path, monkeypatch, capsys):
    host, work, output, identity = archive_inputs(tmp_path)
    allow = host / "verification/protected-qualification-public-allowlist.json"
    allow.parent.mkdir()
    allow.write_bytes(ALLOWLIST.read_bytes())
    folder = tmp_path / "public"
    summary = tmp_path / "summary.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnostic",
            "--host",
            str(host),
            "--folder",
            str(folder),
            "--candidate-sha",
            identity["approved_sha"],
            "--driver-outcome",
            "failure",
        ],
    )
    assert p.main() == 0
    assert "ok=false" in summary.read_text()
    value = p.validate_projection(folder, allow)
    assert not value["raw_primaries_durable"] and not value["full_qualification_accepted"]
    assert "private" not in capsys.readouterr().out
