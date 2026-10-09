"""Package command selection authenticates frozen locators and cannot grant acceptance."""

import json
import sys
import pytest
from openpine.verification import execution_ci as ci
from openpine.verification import stabilization_evidence as ev
from openpine.verification import qualification_public as public
from openpine.verification.execution_identity import hash_file
from openpine.verification.identity import write_json
from tests.test_qualification_projection_boundaries import ALLOWLIST, projection


@pytest.fixture
def sequence(tmp_path, monkeypatch):
    root = tmp_path / "primaries"
    root.mkdir()
    seen = []
    expected = [
        {
            "role": "install",
            "argv": ["unit-supervisor", "unit-install"],
            "cwd": "/frozen/install",
            "inputs": {
                "owner-tool:python": {"path": "/frozen/python", "sha256": "sha256:" + "a" * 64},
                "fixture": {"path": "/frozen/input", "sha256": "sha256:" + "b" * 64},
            },
        },
        {
            "role": "probe",
            "argv": [
                "unit-supervisor",
                "-I",
                "resource-spec",
                "/frozen/python",
                "-c",
                "unit-probe",
            ],
            "cwd": "/frozen/probe",
        },
    ]

    def descriptor(folder, value):
        target = root / "logs" / folder / "command.json"
        write_json(target, value)
        return {"path": target.relative_to(root).as_posix(), "sha256": hash_file(target)}

    install = descriptor("001-install", {"argv": expected[0]["argv"], "cwd": expected[0]["cwd"]})
    probe = descriptor("002-probe", {"argv": expected[1]["argv"], "cwd": expected[1]["cwd"]})
    auxiliary = descriptor(
        "000-version-3.13-wheel", {"argv": ["unit-version"], "cwd": "/frozen/install"}
    )
    # Selectors are unit contracts. This spy asserts exactly which frozen
    # specification is handed to the independently tested real verifier.
    monkeypatch.setattr(
        ev, "verify_command", lambda folder, spec, **kw: seen.append((folder, spec, kw))
    )
    return root, expected, install, probe, auxiliary, descriptor, seen


def test_auxiliary_version_does_not_replace_mandatory_install_or_probe(sequence):
    root, expected, install, probe, auxiliary, desc, seen = sequence
    assert ci.project_package_commands(
        root, [auxiliary, install, probe], expected, "3.13", "/frozen/owner"
    ) == [install, probe]
    assert len(seen) == 3 and seen[1][1] == expected[0] and seen[2][1] == expected[1]
    spec = seen[0][1]
    assert spec["argv"][:4] == [
        "unit-supervisor",
        "-I",
        "/frozen/owner/logs/000-version-3.13-wheel/resources.json",
        "/frozen/python",
    ]
    assert spec["inputs"] == {"fixture": expected[0]["inputs"]["fixture"]}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-install",
        "missing-probe",
        "repeat-version",
        "foreign-folder",
        "unsafe-locator",
        "no-interpreter",
        "version-after-install",
        "wrong-sequence",
        "empty-expected",
        "non-list",
    ],
)
def test_package_sequence_refuses_extra_or_unbound_commands(sequence, mutation):
    root, expected, install, probe, auxiliary, desc, seen = sequence
    supplied = [auxiliary, install, probe]
    if mutation == "missing-install":
        supplied = [auxiliary, probe]
    elif mutation == "missing-probe":
        supplied = [auxiliary, install]
    elif mutation == "repeat-version":
        supplied = [auxiliary, auxiliary, install, probe]
    elif mutation == "foreign-folder":
        supplied[0] = desc("000-private-version", {"argv": ["foreign"], "cwd": "foreign"})
    elif mutation == "unsafe-locator":
        path = root / "000-version-3.13-wheel/command.json"
        write_json(path, {"argv": ["foreign"], "cwd": "foreign"})
        supplied[0] = {"path": path.relative_to(root).as_posix(), "sha256": hash_file(path)}
    elif mutation == "no-interpreter":
        expected[1]["argv"] = ["short"]
    elif mutation == "version-after-install":
        supplied = [install, auxiliary, probe]
    elif mutation == "wrong-sequence":
        supplied = [probe, install]
    elif mutation == "empty-expected":
        expected = []
    else:
        supplied = {}
    with pytest.raises(ValueError):
        ci.project_package_commands(root, supplied, expected, "3.13", "/frozen/owner")


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("private sentinel"),
        OSError("private sentinel"),
        KeyboardInterrupt("private sentinel"),
        SystemExit("private sentinel"),
    ],
)
def test_public_cli_last_boundary_never_prints_private_error(
    tmp_path, monkeypatch, capsys, failure
):
    monkeypatch.setattr(public, "ensure_projection", lambda *a: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "public",
            "--host",
            str(tmp_path),
            "--folder",
            str(tmp_path / "public"),
            "--candidate-sha",
            "a" * 40,
            "--driver-outcome",
            "failure",
        ],
    )
    assert public.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "stage": "publication",
        "error": "projection-invalid",
        "ok": False,
    }


def test_unwritable_step_summary_keeps_private_error_out_of_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))
    public._append_step_summary(["Qualification outcome: ok=false"])
    assert capsys.readouterr().out == "Public diagnostic summary could not be written.\n"


@pytest.mark.parametrize("mutation", ["field-set", "boolean-type", "case-vocabulary"])
def test_revalidated_projection_refuses_foreign_explanations(mutation):
    value = projection()
    if mutation == "field-set":
        value["private"] = "secret"
    elif mutation == "boolean-type":
        value["ok"] = 1
    else:
        value["cases"][0]["case_error"] = "secret"
    with pytest.raises(ValueError):
        public.diagnostic_lines(value, ALLOWLIST)


@pytest.mark.parametrize(
    "mutation", ["symlink", "components", "row-type", "row-sha", "foreign-name"]
)
def test_declared_identity_refuses_untrusted_candidate_template(tmp_path, mutation):
    from tests.test_hosted_primary_archive_boundaries import archive_inputs

    host, work, output, expected = archive_inputs(tmp_path)
    path = host / "candidates/stack-candidate-5.0.0-rc.6.template.json"
    original = json.loads(path.read_text())
    if mutation == "components":
        original["components"] = {}
    elif mutation == "row-type":
        original["components"]["openpine"] = "foreign"
    elif mutation == "row-sha":
        original["components"]["optimizer"]["sha"] = "invalid"
    elif mutation == "foreign-name":
        original["components"]["foreign"] = {"sha": "a" * 40}
    write_json(path, original)
    if mutation == "symlink":
        target = tmp_path / "outside-template.json"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(ValueError):
        public.declared_commits(host, expected["approved_sha"])
