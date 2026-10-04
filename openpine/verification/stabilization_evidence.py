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


def replay_path(historical: str | Path, mappings: dict | None = None) -> Path:
    """Locate historical bytes, never rewrite command/probe identity.

    Maps carry no expected hashes. The consuming owner checks source inventory,
    captured inputs, descriptors or admitted wheel members independently.
    An explicit mapping never falls back to an old runner's filesystem.
    """
    path = Path(historical)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError("unsafe historical owner path")
    if mappings is not None:
        if not isinstance(mappings, dict):
            raise ValueError("owner replay paths must be a locator mapping")
        for old, new in mappings.items():
            for location in (old, new):
                if not isinstance(location, str) or not Path(location).is_absolute() or '..' in Path(location).parts:
                    raise ValueError("unsafe owner replay location")
        matches = [(Path(old), Path(new)) for old, new in mappings.items() if path.is_relative_to(Path(old))]
        if len(matches) != 1:
            raise ValueError("missing/ambiguous owner replay location")
        old, new = matches[0]
        path = new / path.relative_to(old)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("owner replay path crosses a symlink")
    return path.resolve(strict=True)


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
    inputs = expected.get("inputs", {})
    observed = receipt.get("input_provenance", {})
    if set(inputs) != set(observed):
        raise ValueError("command input provenance missing/unbound")
    for name, spec in inputs.items():
        row = observed[name]
        if row.get("source") != spec or row.get("after_sha256") != spec["sha256"] or hash_file(evidence_path(root, row["captured"]["path"])) != spec["sha256"] or row["captured"]["sha256"] != spec["sha256"]:
            raise ValueError("command input provenance changed")
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


def verify_frontend(plan: dict, root: Path, supplied: dict, policy: dict, *, replay_paths: dict | None = None) -> dict:
    binding = supplied.get("binding", {})
    if binding.get("plan_hash") != plan["content_hash"] or binding.get("candidate_hash") != plan.get("source", {}).get("content_hash"):
        raise ValueError("frontend candidate/plan binding is missing or stale")
    expected = {
        name.removeprefix("openpine-ui/"): meta
        for name, meta in plan["source"]["components"]["openpine"]["files"].items()
        if name.startswith("openpine-ui/")
    }
    # The frozen command cwd, not producer-supplied binding metadata, owns
    # the staged source location. OpenAPI export legitimately runs at HOST;
    # every UI consumer (including client drift) must run at the same UI root.
    specifications = policy["commands"]
    test_specs = [s for s in specifications if s.get("role") == "frontend-tests"]
    build_specs = [s for s in specifications if s.get("role") == "frontend-build"]
    if len(test_specs) != 1 or len(build_specs) != 1:
        raise ValueError("frontend test/build source commands are missing/duplicate")
    staged = Path(test_specs[0]["cwd"])
    if not staged.is_absolute() or Path(binding["source_root"]).resolve() != staged.resolve():
        raise ValueError("frontend captured source is detached from execution source")
    if any(Path(s["cwd"]).resolve() != staged.resolve() for s in specifications if s.get("role") != "frontend-openapi"):
        raise ValueError("frontend commands consume different staged source roots")
    from openpine.verification.execution_identity import source_snapshot
    live_staged = replay_path(staged, replay_paths)
    actual = source_snapshot({"frontend": live_staged})["components"]["frontend"]["files"]
    generated = {"dist", "coverage", "test-results", "playwright-report", ".vite", ".vitest"}
    actual = {n: m for n, m in actual.items() if n.split("/")[0] not in generated and not n.endswith(".tsbuildinfo")}
    if not expected or actual != expected or binding.get("source_files") != expected:
        raise ValueError("frontend candidate staged inventory differs from exact plan")
    import copy
    specifications = copy.deepcopy(policy["commands"])
    ui_inputs = {"frontend-source:" + n: {"path": str(staged / n), "sha256": m["sha256"]} for n, m in expected.items()}
    for specification in specifications:
        specification["inputs"] = {**specification.get("inputs", {}), **ui_inputs}
    commands = command_set(root, supplied["commands"], specifications)
    artifacts = {"tests": supplied["tests"], "outputs": supplied.get("outputs", [])}
    test_paths = [a.split("=", 1)[1] for a in test_specs[0]["argv"] if a.startswith("--outputFile.json=")]
    if len(test_paths) != 1 or evidence_path(root, supplied["tests"]["path"]).resolve() != replay_path(test_paths[0], replay_paths):
        raise ValueError("frontend tests descriptor is detached from command output")
    # A matching descriptor copied into receipt metadata is not enough: admit
    # only the complete dist inventory at the independently frozen build cwd.
    built = {p.resolve() for p in (live_staged / "dist").rglob("*") if p.is_file()}
    declared = [evidence_path(root, d["path"]).resolve() for d in artifacts["outputs"]]
    if not built or len(set(declared)) != len(declared) or set(declared) != built:
        raise ValueError("frontend build descriptors differ from execution outputs")
    for receipt in commands:
        if receipt.get("binding") != {"plan_hash": plan["content_hash"], "candidate_hash": plan["source"]["content_hash"], "source_files": expected, "source_root": str(staged)}:
            raise ValueError("frontend candidate producer binding is missing/stale")
    if not any(r.get("artifacts") == artifacts for r in commands):
        raise ValueError("frontend artifacts are unbound to producer receipt")
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


def wheel_members(path: Path, *, max_bytes: int = 512 * 1024**2) -> dict:
    """Read a bounded, unique, regular-file-only wheel inventory."""
    import stat
    from pathlib import PurePosixPath
    with zipfile.ZipFile(path) as archive:
        seen, total, members = set(), 0, {}
        for item in archive.infolist():
            name = item.filename
            pure = PurePosixPath(name)
            mode = item.external_attr >> 16
            if name in seen or pure.is_absolute() or ".." in pure.parts or "\\" in name or ":" in name or stat.S_ISLNK(mode):
                raise ValueError("unsafe/duplicate wheel member")
            seen.add(name)
            total += item.file_size
            if total > max_bytes or len(seen) > 100000:
                raise ValueError("wheel expanded size/member count exceeds bound")
            if not item.is_dir():
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                    raise ValueError("nonregular wheel member")
                members[name] = archive.read(item)
        return members


def verify_packages(plan: dict, root: Path, supplied: dict, policy: dict, *, replay_paths: dict | None = None, source_roots: dict | None = None) -> dict:
    """Admit normal and sdist-rebuilt installations independently."""
    if set(supplied) != {"normal", "rebuilt"} or set(policy) != {"normal", "rebuilt"}:
        raise ValueError("normal and rebuilt artifact/install sets are required")
    results = {}
    for kind in ("normal", "rebuilt"):
        results[kind] = _verify_package_installation(plan, root, supplied[kind], policy[kind], replay_paths=replay_paths, source_roots=source_roots)
    prefixes = [read_artifact(root, supplied[k]["probe"])["prefix"] for k in ("normal", "rebuilt")]
    if Path(prefixes[0]).resolve() == Path(prefixes[1]).resolve():
        raise ValueError("normal and rebuilt installations require separate prefixes")
    if supplied["normal"]["probe"] == supplied["rebuilt"]["probe"]:
        raise ValueError("rebuilt installation needs its own origin probe")
    for component in plan["source"]["components"]:
        if supplied["normal"]["artifacts"][component]["wheel"]["path"] == supplied["rebuilt"]["artifacts"][component]["wheel"]["path"]:
            raise ValueError("rebuilt wheel artifact must be separate from normal wheel")
    return results


def _verify_package_installation(plan: dict, root: Path, supplied: dict, policy: dict, *, replay_paths: dict | None = None, source_roots: dict | None = None) -> dict:
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
    if set(supplied.get("artifacts", {})) != set(plan["source"]["components"]):
        raise ValueError("missing package artifact component")
    wheel_paths = {str(evidence_path(root, row["wheel"]["path"])) for row in supplied["artifacts"].values()}
    wheel_names = {Path(row["wheel"]["path"]).name for row in supplied["artifacts"].values()}
    installed_sets = [
        {str(replay_path(a, replay_paths)) for a in command["argv"]
         if Path(a).is_absolute() and Path(a).name in wheel_names}
        for command, role in zip(commands, roles) if role == "install"
    ]
    if not any(wheel_paths.issubset(paths) for paths in installed_sets):
        raise ValueError("admitted wheels are not bound to an actual complete-stack installation")
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
    if probe["cwd"] != producer["cwd"]:
        raise ValueError("installed probe cwd differs from its producer")
    historical_cwd, historical_prefix = Path(probe["cwd"]), Path(probe["prefix"])
    historical_roots = [Path(p) for p in plan["roots"].values()]
    if any(historical_cwd.is_relative_to(p) or historical_prefix.is_relative_to(p) for p in historical_roots):
        raise ValueError("installed probe overlaps a historical source checkout")
    cwd = replay_path(historical_cwd, replay_paths)
    prefix = replay_path(historical_prefix, replay_paths)
    roots = [Path(p).resolve() for p in (source_roots or plan["roots"]).values()]
    if any(cwd.is_relative_to(p) or prefix.is_relative_to(p) for p in roots):
        raise ValueError("installed probe overlaps a source checkout")
    if set(probe["components"]) != set(plan["source"]["components"]):
        raise ValueError("missing installed component")
    stack_lock = Path((source_roots or plan["roots"])["openpine"]) / "openpine/stack-lock.json"
    if hash_file(stack_lock) != plan["source"]["components"]["openpine"]["files"]["openpine/stack-lock.json"]["sha256"]:
        raise ValueError("replayed stack lock differs from frozen candidate")
    locked_components = read_json(stack_lock)["components"]
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
        historical_origin = Path(observed["origin"])
        if not historical_origin.is_relative_to(historical_prefix):
            raise ValueError("historical installed origin escapes prefix")
        origin = replay_path(historical_origin, replay_paths)
        if origin != prefix / historical_origin.relative_to(historical_prefix):
            raise ValueError("replayed installed origin is detached from prefix")
        if (
            not origin.is_relative_to(prefix)
            or any(origin.is_relative_to(p) for p in roots)
            or observed.get("editable") is not False
        ):
            raise ValueError("editable/source-shadowed package origin")
        files = observed.get("files", {})
        if not files:
            raise ValueError("installed package has no wheel-bound files")
        members = wheel_members(wheel)
        import hashlib

        source_files = plan["source"]["components"][component]["files"]
        import tarfile

        with tarfile.open(sdist, "r:*") as archive:
            source_members, seen, total = {}, set(), 0
            for member in archive:
                if member.name in seen:
                    raise ValueError("duplicate sdist member")
                seen.add(member.name)
                total += member.size
                if total > 512 * 1024**2 or len(seen) > 100000:
                    raise ValueError("sdist expanded size/member count exceeds bound")
                if (
                    member.issym()
                    or member.islnk()
                    or member.name.startswith("/")
                    or ".." in Path(member.name).parts
                    or "\\" in member.name or ":" in member.name
                    or not (member.isfile() or member.isdir())
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
            if Path(relative).is_absolute() or '..' in Path(relative).parts:
                raise ValueError("unsafe installed file relative path")
            installed = replay_path(historical_prefix / relative, replay_paths)
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


def validate_organization(plan: dict, raw: dict, specification: dict) -> None:
    if not isinstance(specification, dict) or raw.get("jobs") != specification.get("jobs"):
        raise ValueError("performance organization jobs differ from declared policy")
    parallel = specification.get("max_parallel_shards")
    if type(parallel) is not int or parallel < 1 or raw.get("max_parallel_shards") != parallel:
        raise ValueError("performance organization concurrency differs")
    from datetime import datetime
    tasks = {t["id"]: t for t in plan["tasks"]}
    events = []
    for index, attempt in enumerate(raw.get("attempts", [])):
        start, end = (datetime.fromisoformat(attempt[k]) for k in ("started_at", "finished_at"))
        if end <= start:
            raise ValueError("performance organization invalid execution interval")
        events.extend([(start, 1, index, tasks[attempt["task"]]), (end, 0, index, tasks[attempt["task"]])])
    active = {}
    for _, entering, index, task in sorted(events, key=lambda e: e[:3]):
        if entering:
            active[index] = task
        else:
            active.pop(index)
        groups = [t["exclusive_group"] for t in active.values() if t["exclusive_group"]]
        if len(active) > parallel or len({t["component"] for t in active.values()}) > 1 or sum(t["cpu_slots"] for t in active.values()) > raw["jobs"] or len(groups) != len(set(groups)):
            raise ValueError("performance organization actual worker overlap violates reservations")
    maximum = specification.get("max_shards_per_task")
    if type(maximum) is not int or maximum < 1 or any(len(t["shards"]) > maximum for t in plan["tasks"]):
        raise ValueError("performance organization shard layout differs")
    profile = raw.get("resource_profile", {})
    for key in ("cpu_quota", "memory_limit_bytes", "address_space_limit_bytes"):
        if key not in specification or key not in profile or profile[key] != specification[key]:
            raise ValueError("performance resource budget differs: " + key)
    cap = specification.get("cpu_frequency_max_khz")
    frequencies = profile.get("cpu_frequency_max_khz")
    if "cpu_frequency_max_khz" not in specification or not isinstance(frequencies, list) or (cap is not None and (not frequencies or any(type(v) is not int or v != cap for v in frequencies))):
        raise ValueError("performance resource CPU frequency budget differs")


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
            folder = evidence_folder(root, row["campaign"])
            validate_organization(sample_plan, read_json(folder / "run.json"), policy["organization_limits"][organization])
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
