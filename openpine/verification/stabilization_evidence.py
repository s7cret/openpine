"""Raw adapters used by stage_gate, not an alternative acceptance owner.

Command specifications and expected semantic results belong to the frozen
source policy. Caller supplied PASS flags/expected values are never authority.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from openpine.verification.execution_campaign import aggregate_campaign
from openpine.verification.execution_identity import (
    evidence_path,
    hash_file,
    read_artifact,
)
from openpine.verification.identity import digest, read_json, verify


def evidence_folder(root: Path, relative: str) -> Path:
    path = evidence_path(root, relative, must_exist=False)
    if not path.is_dir():
        raise ValueError("missing evidence directory: " + relative)
    return path


def verify_command(root: Path, expected: dict, *, expected_stdout=None) -> dict:
    """Re-read run_logged's command and BOTH logs against frozen argv/cwd."""
    receipt = read_json(evidence_path(root, "command.json"))
    verify(receipt, "openpine.execution_command.v1")
    if (
        receipt.get("argv") != expected["argv"]
        or receipt.get("cwd") != expected["cwd"]
        or receipt.get("status") != "completed"
        or type(receipt.get("returncode")) is not int
        or receipt["returncode"] != 0
        or receipt.get("error") is not None
        or receipt.get("ok") is not True
    ):
        raise ValueError("command identity or successful completion mismatch")
    files = receipt.get("files", {})
    if not {"stdout.log", "stderr.log"}.issubset(files):
        raise ValueError("both primary command logs are required")
    for name, checksum in files.items():
        if hash_file(evidence_path(root, name)) != checksum:
            raise ValueError("command input/log checksum mismatch")
    if expected_stdout is not None:
        actual = json.loads(evidence_path(root, "stdout.log").read_text())
        if actual != expected_stdout:
            raise ValueError(
                "raw command output differs from independent expected result"
            )
    return receipt


def command_set(root: Path, supplied: list, expected: list) -> list:
    if not expected or len(supplied) != len(expected):
        raise ValueError("missing independently specified owner commands")
    results = []
    for descriptor, specification in zip(supplied, expected):
        receipt = read_artifact(root, descriptor)
        command_root = evidence_path(root, descriptor["path"]).parent
        rebuilt = verify_command(
            command_root,
            specification,
            expected_stdout=specification.get("expected_stdout"),
        )
        if receipt != rebuilt:
            raise ValueError("command descriptor mismatch")
        results.append(rebuilt)
    return results


def verify_workers(plan: dict, campaign: Path, run_id: str, policy: dict) -> dict:
    report = aggregate_campaign(
        plan, campaign, expected_plan_hash=plan["content_hash"], expected_run_id=run_id
    )
    if not report["pytest_scope_passed"]:
        raise ValueError("worker execution campaign failed")
    required = policy.get("nodes", {})
    if not required or not all(required.values()):
        raise ValueError("no reviewed protected-worker denominator")
    for component, nodes in required.items():
        tasks = [t for t in plan["tasks"] if t["component"] == component]
        if not tasks or any(not set(nodes).issubset(t["nodeids"]) for t in tasks):
            raise ValueError("missing protected-worker obligation/interpreter")
    return {"aggregate_hash": report["content_hash"], "nodes_hash": digest(required)}


def verify_reconciliation(plan: dict, host: Path, policy: dict) -> dict:
    """Decisions are source inputs; mappings must resolve in this exact candidate."""
    register = read_json(host / policy["register"])
    if hash_file(host / policy["register"]) != policy["sha256"]:
        raise ValueError("reconciliation register changed")
    rows = register.get("rows", [])
    if (
        not rows
        or [r["id"] for r in rows] != policy["required_ids"]
        or len({r["id"] for r in rows}) != len(rows)
    ):
        raise ValueError("missing/duplicate branch reconciliation decision")
    for row in rows:
        if row["decision"] not in {
            "already_ported",
            "port_required",
            "superseded_with_mapping",
            "rejected_with_reason",
        }:
            raise ValueError("unknown reconciliation decision")
        if row["decision"] == "port_required" or not row.get("reason"):
            raise ValueError("unresolved branch reconciliation work")
        mappings = row.get("mappings", [])
        if row["decision"] != "rejected_with_reason" and not mappings:
            raise ValueError("decision has no actual implementation/test mapping")
        for mapping in mappings:
            files = plan["source"]["components"][mapping["component"]]["files"]
            if files.get(mapping["path"], {}).get("sha256") != mapping["sha256"]:
                raise ValueError("stale reconciliation implementation mapping")
            nodes = mapping.get("nodes", [])
            tasks = [t for t in plan["tasks"] if t["component"] == mapping["component"]]
            if nodes and (
                not tasks or any(not set(nodes).issubset(t["nodeids"]) for t in tasks)
            ):
                raise ValueError("reconciliation test mapping was not executed")
    return {"register_sha256": policy["sha256"], "decisions": len(rows)}


def verify_frontend(root: Path, supplied: dict, policy: dict) -> dict:
    commands = command_set(root, supplied["commands"], policy["commands"])
    tests = read_artifact(root, supplied["tests"])
    # Vitest/Jest JSON is a raw test inventory, unlike a generic {passed:true}.
    required = policy["test_names"]
    assertions = [
        a for suite in tests["testResults"] for a in suite["assertionResults"]
    ]
    names = [a["fullName"] for a in assertions]
    if (
        not required
        or sorted(names) != sorted(required)
        or len(set(names)) != len(names)
    ):
        raise ValueError(
            "frontend executed inventory differs from reviewed obligations"
        )
    if (
        any(a["status"] != "passed" for a in assertions)
        or tests["numFailedTests"] != 0
        or tests["numPassedTests"] != len(required)
        or tests["numTotalTests"] != len(required)
    ):
        raise ValueError("frontend raw test results are unsuccessful")
    outputs = supplied.get("outputs", [])
    if len(outputs) != policy["build_output_count"] or not outputs:
        raise ValueError("frontend build outputs are missing")
    for desc in outputs:
        if (
            not evidence_path(root, desc["path"]).read_bytes()
            or hash_file(evidence_path(root, desc["path"])) != desc["sha256"]
        ):
            raise ValueError("frontend build output corruption")
    return {
        "commands": [r["content_hash"] for r in commands],
        "tests_sha256": supplied["tests"]["sha256"],
    }


def verify_packages(plan: dict, root: Path, supplied: dict, policy: dict) -> dict:
    """Require builds/install/API commands plus wheel-bound installed file origins."""
    commands = command_set(root, supplied["commands"], policy["commands"])
    roles = [s.get("role") for s in policy["commands"]]
    required_roles = {
        "wheel",
        "sdist",
        "sdist-wheel",
        "install",
        "probe",
        "compile",
        "run",
        "library",
        "resources",
        "missing-resource",
        "tampered-resource",
        "source-shadowing",
    }
    if not required_roles.issubset(roles):
        raise ValueError("installed package obligation denominator is incomplete")
    for specification in policy["commands"]:
        if specification.get("role") in {
            "compile",
            "run",
            "library",
            "resources",
            "missing-resource",
            "tampered-resource",
            "source-shadowing",
        }:
            expected = specification.get("expected_stdout")
            if (
                not isinstance(expected, dict)
                or not expected
                or set(expected).issubset({"ok", "passed", "status"})
            ):
                raise ValueError(
                    "package semantic commands need independent concrete expected results"
                )
    probe_indices = [i for i, role in enumerate(roles) if role == "probe"]
    if len(probe_indices) != 1:
        raise ValueError("exactly one raw installed origin probe is required")
    producer = commands[probe_indices[0]]
    command_root = evidence_path(
        root, supplied["commands"][probe_indices[0]]["path"]
    ).parent
    if (
        evidence_path(root, supplied["probe"]["path"]) != command_root / "stdout.log"
        or "-I" not in producer["argv"]
        or "PYTHONPATH" in producer["environment_keys"]
    ):
        raise ValueError("probe is not the actual isolated command stdout")
    probe = read_artifact(root, supplied["probe"])
    if probe.get("schema_id") != "openpine.installed_origins.v1":
        raise ValueError("invalid installed origin probe schema")
    required_version = policy["python"]
    versions = {e["identity"]["python"] for e in plan["environments"].values()}
    if probe["python"] not in versions or not probe["python"].startswith(
        required_version + "."
    ):
        raise ValueError("installed package interpreter differs from executed plan")
    environment_hashes = {
        e["identity"]["executable_sha256"]
        for e in plan["environments"].values()
        if e["identity"]["python"] == probe["python"]
    }
    if probe.get("executable_sha256") not in environment_hashes:
        raise ValueError("installed probe used a different interpreter binary")
    if probe["isolated"] is not True or probe["pythonpath"] is not None:
        raise ValueError("installed probe must use -I without PYTHONPATH")
    cwd = Path(probe["cwd"]).resolve(strict=True)
    prefix = Path(probe["prefix"]).resolve(strict=True)
    roots = [Path(p).resolve() for p in plan["roots"].values()]
    if any(cwd.is_relative_to(p) or prefix.is_relative_to(p) for p in roots):
        raise ValueError("installed probe overlaps a source checkout")
    if set(probe["components"]) != set(plan["source"]["components"]):
        raise ValueError("missing installed component")
    locked_components = read_json(
        Path(plan["roots"]["openpine"]) / "openpine/stack-lock.json"
    )["components"]
    aliases = {
        "marketdata_provider": "marketdata-provider",
        "openpine_contracts": "openpine-contracts",
    }
    locked_versions = {
        aliases.get(row["name"], row["name"]): row["version"]
        for row in locked_components
    }
    for component, observed in probe["components"].items():
        if observed["version"] != locked_versions.get(component):
            raise ValueError(
                "installed distribution version differs from active stack lock"
            )
        wheel = evidence_path(root, supplied["artifacts"][component]["wheel"]["path"])
        sdist = evidence_path(root, supplied["artifacts"][component]["sdist"]["path"])
        for kind, path in (("wheel", wheel), ("sdist", sdist)):
            if hash_file(path) != supplied["artifacts"][component][kind]["sha256"]:
                raise ValueError("package artifact hash mismatch")
        origin = Path(observed["origin"]).resolve(strict=True)
        if (
            not origin.is_relative_to(prefix)
            or any(origin.is_relative_to(p) for p in roots)
            or observed.get("editable") is not False
        ):
            raise ValueError("editable/source-shadowed package origin")
        files = observed.get("files", {})
        if not files:
            raise ValueError("installed package has no wheel-bound files")
        with zipfile.ZipFile(wheel) as archive:
            members = {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            }
        import hashlib

        source_files = plan["source"]["components"][component]["files"]
        import tarfile

        with tarfile.open(sdist, "r:*") as archive:
            source_members = {}
            for member in archive.getmembers():
                if (
                    member.issym()
                    or member.islnk()
                    or member.name.startswith("/")
                    or ".." in Path(member.name).parts
                ):
                    raise ValueError("unsafe sdist member")
                if member.isfile():
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("missing sdist member bytes")
                    relative = "/".join(Path(member.name).parts[1:])
                    if relative in source_members:
                        raise ValueError("duplicate sdist source member")
                    source_members[relative] = stream.read()
        if (
            "pyproject.toml" not in source_members
            or "sha256:" + hashlib.sha256(source_members["pyproject.toml"]).hexdigest()
            != source_files["pyproject.toml"]["sha256"]
        ):
            raise ValueError("sdist build inputs differ from current candidate")
        module = Path(observed["origin"]).parent.name
        for name, meta in source_files.items():
            if name.startswith(module + "/") and name.endswith(".py"):
                if name not in members or name not in source_members:
                    raise ValueError("required package source absent from wheel/sdist")
                if (
                    "sha256:" + hashlib.sha256(source_members[name]).hexdigest()
                    != meta["sha256"]
                ):
                    raise ValueError("sdist source bytes differ from frozen candidate")
        for relative, checksum in files.items():
            installed = prefix / relative
            if (
                not installed.resolve(strict=True).is_relative_to(prefix)
                or hash_file(installed) != checksum
            ):
                raise ValueError("installed package content was changed")
            matches = [
                name
                for name, data in members.items()
                if installed.as_posix().endswith("/" + name)
                and "sha256:" + hashlib.sha256(data).hexdigest() == checksum
            ]
            if len(matches) != 1:
                raise ValueError("installed file differs from admitted wheel")
            name = matches[0]
            if name in source_files and source_files[name]["sha256"] != checksum:
                raise ValueError("wheel contains stale candidate source/resource")
        for name, meta in source_files.items():
            if (
                name in members
                and "sha256:" + hashlib.sha256(members[name]).hexdigest()
                != meta["sha256"]
            ):
                raise ValueError("wheel source bytes differ from frozen candidate")
        for resource in policy.get("resources", {}).get(component, []):
            if resource not in members or not any(
                (prefix / rel).as_posix().endswith("/" + resource) for rel in files
            ):
                raise ValueError("required installed wheel resource is absent")
    return {"probe_sha256": supplied["probe"]["sha256"], "python": probe["python"]}


def verify_performance(plan: dict, root: Path, supplied: dict, policy: dict) -> dict:
    from openpine.verification.execution_performance import (
        compare_campaigns,
        validate_performance_scope,
    )

    samples = {}
    for organization in ("before", "after"):
        samples[organization] = []
        for row in supplied[organization]:
            sample_plan = read_artifact(root, row["plan"])
            validate_performance_scope(plan, sample_plan)
            samples[organization].append(
                (sample_plan, evidence_folder(root, row["campaign"]), row["run_id"])
            )
    report = compare_campaigns(
        samples["before"],
        samples["after"],
        reference_profile=policy["reference_profile"],
        target_speedup=policy["target_speedup"],
    )
    if not report["target_met"]:
        raise ValueError("performance improvement budget remains open")
    return report
