"""Real interpreter, archive, and owner-control-plane CI contracts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from openpine.verification import execution_ci
from openpine.verification.execution_coverage import verify_task_coverage
from openpine.verification.execution_identity import environment_snapshot, hash_file, source_snapshot, write_once_json
from openpine.verification.execution_plan import make_plan
from openpine.verification.identity import read_json, seal
from openpine.verification.pytest_gate import collection_hash


HOST = Path(__file__).resolve().parents[1]


def _run(
    argv: list[str], *, cwd: Path, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed test argv; each return status is checked.
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        env=environment,
        text=True,
        timeout=120,
    )


def _source_roots(tmp_path: Path, *, owner: bool = False) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for name in execution_ci.COMPONENTS:
        root = tmp_path / "sources" / name
        root.mkdir(parents=True)
        roots[name] = root
        (root / "pyproject.toml").write_text(
            "[project]\nname = \"" + name + "\"\nversion = \"0.0.0\"\n",
            encoding="utf-8",
        )
    package = roots["openpine"] / "openpine"
    package.mkdir()
    shutil.copy2(HOST / "openpine/__init__.py", package / "__init__.py")
    shutil.copytree(
        HOST / "openpine/verification",
        package / "verification",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    docs = roots["openpine"] / "docs"
    docs.mkdir()
    for name in ("RC6_LIFECYCLE_SOURCES.json", "RC6_REVIEW_36.json"):
        shutil.copy2(HOST / "docs" / name, docs / name)
    if owner:
        optimizer = roots["optimizer"]
        (optimizer / "pyproject.toml").write_text(
            "[project]\nname = \"optimizer\"\nversion = \"0.0.0\"\n"
            "[tool.coverage.run]\nbranch = true\nsource = [\"optimizer\"]\n",
            encoding="utf-8",
        )
        module = optimizer / "optimizer"
        module.mkdir()
        (module / "__init__.py").write_text(
            "def square(value: int) -> int:\n    return value * value\n",
            encoding="utf-8",
        )
        tests = optimizer / "tests"
        tests.mkdir()
        (tests / "test_owner_contract.py").write_text(
            "import json\nimport os\nfrom pathlib import Path\n\n"
            "from optimizer import square\n\n"
            "def test_square_has_independent_integer_result() -> None:\n"
            "    observed = {\"operand\": 9, \"expected_square\": 81}\n"
            "    evidence = Path(os.environ[\"OPENPINE_STAGE1_EVIDENCE\"]) / \"observations\"\n"
            "    evidence.mkdir(parents=True)\n"
            "    (evidence / \"owner-contract.json\").write_text(json.dumps(observed), encoding=\"utf-8\")\n"
            "    assert square(observed[\"operand\"]) == observed[\"expected_square\"]\n",
            encoding="utf-8",
        )
    return roots


def _owner_plan(tmp_path: Path) -> tuple[dict, Path, dict[str, Path]]:
    roots = _source_roots(tmp_path, owner=True)
    source = source_snapshot(roots)
    environment = environment_snapshot()
    nodeid = "tests/test_owner_contract.py::test_square_has_independent_integer_result"
    lane = "py" + "".join(map(str, sys.version_info[:2]))
    inventories = {
        "optimizer@" + lane: {
            "nodeids": [nodeid],
            "node_markers": {nodeid: []},
            "reviewed_lock": {"count": 1, "deselected": 0, "sha256": collection_hash([nodeid])},
            "deselected": 0,
            "source_hash": source["content_hash"],
            "environment_hash": environment["content_hash"],
        }
    }
    policy = {
        "components": {
            "optimizer": {
                "dependencies": [],
                "pythons": [f"{sys.version_info.major}.{sys.version_info.minor}"],
                "timeout_seconds": 60,
            }
        }
    }
    plan = make_plan(
        profile="component",
        policy=policy,
        roots=roots,
        source=source,
        inventories=inventories,
        environments={lane: {"identity": environment, "executable": sys.executable}},
        requested=["optimizer"],
        shard_count=1,
        coverage=True,
    )
    plan = seal(
        {
            **{key: value for key, value in plan.items() if key != "content_hash"},
            "source_commits": {name: "a" * 40 for name in execution_ci.COMPONENTS},
        }
    )
    path = tmp_path / "plan.json"
    write_once_json(path, plan)
    return plan, path, roots


def test_ci_task_runs_real_child_pytest_and_owner_coverage(tmp_path: Path) -> None:
    plan, plan_path, roots = _owner_plan(tmp_path)
    output = tmp_path / "task-output"

    task_id = "optimizer@py" + "".join(map(str, sys.version_info[:2]))
    result = execution_ci.run_ci_task(
        plan,
        plan_path,
        roots,
        task_id,
        output,
        "real-owner-contract",
    )

    assert result == {
        "schema_id": "openpine.ci_task_result.v1",
        "ok": True,
        "pytest_scope_passed": True,
        "coverage_scope_passed": True,
        "task": task_id,
        "plan_hash": plan["content_hash"],
        "run_id": "real-owner-contract",
        "scope": "single component/interpreter only",
        "full_stage_accepted": False,
        "content_hash": result["content_hash"],
    }
    execution = read_json(output / "run.json")
    assert execution["attempts"][0]["status"] == "completed"
    assert execution["attempts"][0]["returncode"] == 0
    assert verify_task_coverage(
        plan,
        output,
        task_id,
        output / "coverage-owner",
        run_id="real-owner-contract",
    )["ok"] is True
    coverage = read_json(output / "coverage-owner/coverage.json")
    assert coverage["files"]["optimizer/__init__.py"]["summary"]["percent_covered"] == 100.0
    descriptor = read_json(output / "ci-task.json")
    assert descriptor["task"] == task_id
    assert descriptor["run_id"] == "real-owner-contract"


def test_foundation_exports_real_owner_evidence_but_refuses_incomplete_component_set(
    tmp_path: Path,
) -> None:
    plan, plan_path, roots = _owner_plan(tmp_path)
    task_id = "optimizer@py" + "".join(map(str, sys.version_info[:2]))
    fragment = tmp_path / "fragment"
    execution_ci.run_ci_task(plan, plan_path, roots, task_id, fragment, "foundation-real-owner")
    foundation = tmp_path / "foundation"

    with pytest.raises(FileNotFoundError):
        execution_ci.finalize_foundation(
            plan,
            roots,
            [fragment],
            foundation,
            "foundation-real-owner",
        )

    evidence = foundation / "suites" / task_id.split("@", 1)[1]
    assert read_json(evidence / "optimizer.inventory.json")["ok"] is True
    assert read_json(evidence / "source-pins.json") == read_json(
        roots["openpine"] / "docs/RC6_LIFECYCLE_SOURCES.json"
    )
    assert read_json(evidence / "observations/owner-contract.json") == {
        "operand": 9,
        "expected_square": 81,
    }
    assert (foundation / "owner-coverage" / task_id / "receipt.json").is_file()

    # The builtin verifier reads a separate evidence root. It must receive the
    # existing foundation receipt, not silently treat missing pins as stale.
    merged = tmp_path / "merged"
    owner = merged / "owner.json"
    owner.parent.mkdir()
    owner.write_text('{"observed": 81}', encoding="utf-8")
    write_once_json(merged / "run.json", {"attempts": [{
        "task": task_id,
        "artifacts": {"owner:observations/contract.json": {
            "path": "owner.json", "sha256": hash_file(owner),
        }},
    }]})
    builtin = tmp_path / "builtin"
    execution_ci.retain_builtin_owner_evidence(
        merged, foundation / "suites", builtin, task_id.split("@", 1)[1],
        roots["openpine"] / "docs/RC6_LIFECYCLE_SOURCES.json",
    )
    assert read_json(builtin / "source-pins.json") == read_json(evidence / "source-pins.json")
    assert read_json(builtin / "observations/contract.json") == {"observed": 81}
    (builtin / "source-pins.json").write_text('{"invalid":"pin"}', encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting builtin owner source pins"):
        execution_ci.retain_builtin_owner_evidence(
            merged, foundation / "suites", builtin, task_id.split("@", 1)[1],
            roots["openpine"] / "docs/RC6_LIFECYCLE_SOURCES.json",
        )


def test_execute_command_rechecks_restored_candidate_before_running_child(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    plan, plan_path, roots = _owner_plan(tmp_path)
    task_id = "optimizer@py" + "".join(map(str, sys.version_info[:2]))
    restored = tmp_path / "restored.json"
    write_once_json(
        restored,
        {
            "roots": {name: str(path) for name, path in roots.items()},
            "environment": environment_snapshot(),
            "source_commits": plan["source_commits"],
        },
    )
    (roots["optimizer"] / "optimizer/__init__.py").write_text(
        "def square(value: int) -> int:\n    return value + value\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        ci_action="execute",
        plan=plan_path,
        restored=restored,
        task=task_id,
        fragment=[],
        output=tmp_path / "must-not-run",
        run_id="changed-candidate",
    )

    with pytest.raises(ValueError, match="restored interpreter, source or producer commits changed"):
        execution_ci.run_ci_command(args)

    assert not args.output.exists()


def test_prepare_rejects_a_real_dirty_git_host_before_clone_or_install(tmp_path: Path) -> None:
    host = tmp_path / "host"
    (host / "docs").mkdir(parents=True)
    pins = {name: "b" * 40 for name in execution_ci.COMPONENTS if name != "openpine"}
    (host / "docs/RC6_LIFECYCLE_SOURCES.json").write_text(json.dumps(pins), encoding="utf-8")
    _run(["git", "init", "--quiet"], cwd=host)
    _run(["git", "config", "user.email", "ci@example.invalid"], cwd=host)
    _run(["git", "config", "user.name", "CI contract"], cwd=host)
    _run(["git", "add", "docs/RC6_LIFECYCLE_SOURCES.json"], cwd=host)
    _run(["git", "commit", "--quiet", "-m", "initial pinned host"], cwd=host)
    (host / "docs/RC6_LIFECYCLE_SOURCES.json").write_text(json.dumps(pins, sort_keys=True), encoding="utf-8")

    with pytest.raises(RuntimeError, match="command failed; raw receipt"):
        execution_ci.prepare(
            host,
            tmp_path / "prepared",
            f"{sys.version_info.major}.{sys.version_info.minor}",
        )

    first = read_json(tmp_path / "prepared/commands/0000/command.json")
    rejected = read_json(tmp_path / "prepared/commands/0001/command.json")
    assert first["ok"] is True and first["argv"][-2:] == ["rev-parse", "HEAD"]
    assert rejected["ok"] is False and rejected["returncode"] == 1
    assert rejected["argv"][-3:] == ["diff", "--exit-code", "HEAD"]
    assert not (tmp_path / "prepared/stack/openpine").exists()


def test_restore_recreates_a_real_venv_from_archived_source_and_local_wheelhouse(tmp_path: Path) -> None:
    roots = _source_roots(tmp_path)
    openpine_root = roots["openpine"]
    (openpine_root / "pyproject.toml").write_text(
        "[build-system]\nrequires = [\"setuptools\", \"wheel\"]\n"
        "build-backend = \"setuptools.build_meta\"\n"
        "[project]\nname = \"openpine\"\nversion = \"0.0.0\"\n"
        "[tool.setuptools.packages.find]\ninclude = [\"openpine*\"]\n",
        encoding="utf-8",
    )
    commits = {}
    for name, root in roots.items():
        _run(["git", "init", "--quiet"], cwd=root)
        _run(["git", "add", "."], cwd=root)
        _run(
            ["git", "-c", "user.email=ci@example.invalid", "-c", "user.name=CI fixture", "commit", "--quiet", "-m", "fixture"],
            cwd=root,
        )
        commits[name] = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = execution_ci.create_source_archive(roots, bundle / "sources.tar.gz")
    for name, root in roots.items():
        execution_ci.export_git_provenance(root, bundle / "git" / (name + ".bundle"), commits[name])
    wheelhouse = bundle / "wheelhouse"
    wheelhouse.mkdir()
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            str(openpine_root),
        ],
        cwd=tmp_path,
    )
    probe = tmp_path / "independent-probe"
    venv.EnvBuilder(with_pip=True, symlinks=True).create(probe)
    probe_python = probe / "bin/python"
    probe_environment = dict(os.environ)
    probe_environment.pop("PYTHONPATH", None)
    _run(
        [
            str(probe_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--find-links",
            str(wheelhouse),
            "openpine==0.0.0",
        ],
        cwd=tmp_path,
        environment=probe_environment,
    )
    observed = json.loads(
        _run(
            [
                str(probe_python),
                "-c",
                "import json; from openpine.verification.execution_identity import environment_snapshot; "
                "print(json.dumps(environment_snapshot()))",
            ],
            cwd=tmp_path,
            environment=probe_environment,
        ).stdout
    )
    (bundle / "locked-versions.txt").write_text("openpine==0.0.0\n", encoding="utf-8")
    pins = {name: commits[name] for name in execution_ci.COMPONENTS if name != "openpine"}
    report = execution_ci.bundle_manifest(
        bundle,
        source=source,
        environment=observed,
        source_pins=pins,
        host_commit=commits["openpine"],
    )

    restored_report, restored_roots, executable = execution_ci.restore(
        bundle,
        tmp_path / "restored-work",
        expected_source_hash=source["content_hash"],
        expected_commits=commits,
    )

    assert restored_report["content_hash"] == report["content_hash"]
    assert source_snapshot(restored_roots) == source
    installed = _run(
        [
            str(executable),
            "-I",
            "-c",
            "import openpine, sys; assert openpine.__file__.startswith(sys.prefix); print(openpine.__file__)",
        ],
        cwd=tmp_path,
    ).stdout.strip()
    assert "/site-packages/openpine/" in installed
    restored = read_json(tmp_path / "restored-work/restored.json")
    assert restored["candidate_hash"] == source["content_hash"]
    assert restored["environment"] == observed
    assert restored["source_commits"] == commits
