"""Real, deliberately miniature eight-owner verifier fixture; not product evidence."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from openpine.verification.architecture import COMPONENTS
from openpine.verification.execution_campaign import (
    descriptor,
    export_suite_receipts,
    run_campaign,
)
from openpine.verification.execution_identity import (
    clean_environment,
    environment_snapshot,
    hash_file,
    source_snapshot,
    write_once_json,
)
from openpine.verification.execution_plan import make_plan
from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json, seal
from openpine.verification.pytest_gate import collection_hash
from openpine.verification.stage_gate import STABILIZATION_GATES, run_stage_gate

HOST = Path(__file__).resolve().parents[1]


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)


def freeze_fixture_environment(roots, folder, inputs):
    """Observe the complete environment in the actual child source scope."""
    environment = clean_environment(
        {name: str(path) for name, path in roots.items()}, folder / "private"
    )
    code = (
        "import json; from openpine.verification.execution_identity import "
        "environment_snapshot; print(json.dumps(environment_snapshot()))"
    )
    result = run_logged(
        [sys.executable, "-B", "-c", code],
        cwd=roots["openpine"],
        output=folder / "command",
        env=environment,
        inputs=inputs,
        timeout=60,
    )
    if not result["ok"]:
        raise AssertionError("fixture environment probe failed: " + str(folder))
    return json.loads((folder / "command" / "stdout.log").read_text())


def build_fixture(base, *, portable=False, owner_namespaces=False, product=False):
    stack, evidence = base / "stack", base / "evidence"
    evidence.mkdir()
    roots = {name: stack / name for name in COMPONENTS}
    host = roots["openpine"]
    modules = {name: row[0] for name, row in COMPONENTS.items()}
    nodes = ["test_minimal.py::test_value"]
    lock = {"count": 1, "sha256": collection_hash(nodes), "deselected": 0}
    for name, root in roots.items():
        module = modules[name]
        put(
            root / module / "__init__.py",
            '__version__ = "1.0.0"\n\ndef add(a,b):\n    return a+b\n',
        )
        put(root / module / "fixture-resource.json", {"value": 4})
        put(
            root / "test_minimal.py",
            f'import subprocess, sys\nfrom {module} import add\ndef test_value():\n    assert add(2,2)==4\n    p=subprocess.run([sys.executable,"-I","-c","print(2+2)"],capture_output=True,text=True)\n    assert p.returncode==0 and p.stdout.strip()=="4"\n',
        )
        put(
            root / "pyproject.toml",
            f'[build-system]\nrequires=["setuptools", "wheel"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="{name.replace("_", "-")}"\nversion="1.0.0"\n[tool.setuptools.packages.find]\ninclude=["{module}*"]\n[tool.setuptools.package-data]\n"*"=["*.json"]\n[tool.coverage.run]\nsource=["{module}"]\n[tool.coverage.report]\nfail_under=0\n',
        )
    shutil.copytree(
        HOST / "openpine/verification",
        host / "openpine/verification",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # Tiny, independently authored foundation corpus, observed by a real command.
    for relative in (
        "verification/stages.json",
        "docs/RC6_REVIEW_36.json",
        "docs/OPENPINE_5_0_REMAINING_SPEC_2026-10-06.md",
        "docs/RC6_LIFECYCLE_SOURCES.json",
        "verification/stage2-remaining-matrix.json",
        "verification/stage2-remaining-matrix-lock.json",
    ):
        path = host / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOST / relative, path)
    put(
        host / "docs/RC6_LIFECYCLE_SOURCES.json",
        {name: "1" * 40 for name in COMPONENTS if name != "openpine"},
    )
    put(
        host / "openpine/stack-lock.json",
        {"components": [{"name": name, "version": "1.0.0"} for name in COMPONENTS]},
    )
    put(host / "verification/inventory.json", {name: lock for name in COMPONENTS})
    put(
        host / "verification/capability-policy.json",
        {
            "schema_id": "openpine.required_capabilities.v1",
            "minimum_rows": 0,
            "required": [],
        },
    )
    corpus = host / "verification/corpus-v1"
    for filename, value in [
        ("source.pine", '//@version=6\nindicator("fixture")\nplot(2+2)\n'),
        ("data.json", {}),
        ("settings.json", {}),
        ("expected.json", {"compile": True, "events": [{"value": 4}]}),
    ]:
        put(corpus / filename, value)

    def corpus_desc(filename):
        return {
            "path": filename,
            "sha256": hash_file(corpus / filename).removeprefix("sha256:"),
        }

    manifest = seal(
        {
            "schema_id": "openpine.conformance_corpus.v1",
            "revision": "synthetic-verifier-only",
            "profile": "engineering",
            "cases": [
                {
                    "id": "arithmetic",
                    "pine_version": 6,
                    "layer": "runtime",
                    "weight": 1,
                    "critical": True,
                    "source": corpus_desc("source.pine"),
                    "data": corpus_desc("data.json"),
                    "settings": corpus_desc("settings.json"),
                    "expected": corpus_desc("expected.json"),
                    "oracle": {
                        "kind": "manual_fixture",
                        "provenance": "Independent elementary arithmetic fixture, not language acceptance",
                    },
                    "tolerance": {"absolute": 0, "relative": 0},
                }
            ],
        }
    )
    put(corpus / "manifest.json", manifest)
    put(
        host / "verification/corpus-lock.json",
        {"content_hash": manifest["content_hash"]},
    )
    register = {
        "rows": [
            {
                "id": "fixture",
                "decision": "already_ported",
                "reason": "Synthetic verifier mapping",
                "mappings": [
                    {
                        "component": "openpine",
                        "path": "test_minimal.py",
                        "sha256": hash_file(host / "test_minimal.py"),
                        "nodes": nodes,
                    }
                ],
            }
        ]
    }
    put(host / "verification/fixture-reconciliation.json", register)
    specs = {
        "branch-reconciliation": {
            "register": "verification/fixture-reconciliation.json",
            "sha256": hash_file(host / "verification/fixture-reconciliation.json"),
            "required_ids": ["fixture"],
        },
        "foundation": {"stack_root": str(stack)},
        "protected-workers": {"nodes": {"openpine": nodes}},
        "coverage": {},
    }
    entries = {"branch-reconciliation": {}, "protected-workers": {}}
    version = ".".join(environment_snapshot()["python"].split(".")[:2])
    environment_name = 'py' + version.replace('.', '') if owner_namespaces else 'py'
    # Actually build and install all eight minimal distributions in a new venv.
    wheelhouse = evidence / "wheelhouse"
    wheelhouse.mkdir()
    commands, expected_commands = [], []
    helper = base / "fixture-helper.py"
    helper.write_text("# Immutable miniature harness input\n")
    harness_inputs = {"fixture-helper.py": {"path": str(helper), "sha256": hash_file(helper)}}
    specs["package_harness_inputs"] = dict(harness_inputs)
    put(host / "openpine-ui/package.json", {"name": "fixture-ui"})
    scratch = base / "scratch"
    scratch.mkdir()
    clean = {
        "PATH": os.environ["PATH"],
        "HOME": str(base),
        "TMPDIR": str(scratch),
    }

    def command(role, argv, *, cwd=None, expected_stdout=None, binding=None, artifacts=None):
        cwd = cwd or (evidence if owner_namespaces else base)
        folder = evidence / f"command-{len(commands)}"
        actual = run_logged(argv, cwd=cwd, output=folder, env=clean, timeout=180, inputs=harness_inputs, binding=binding, artifacts=artifacts)
        if not actual["ok"]:
            raise AssertionError(
                (
                    actual,
                    (folder / "stderr.log").read_text(),
                    (folder / "stdout.log").read_text(),
                )
            )
        commands.append(descriptor(evidence, folder / "command.json"))
        spec = {"role": role, "argv": argv, "cwd": str(cwd), "inputs": dict(harness_inputs)}
        if expected_stdout is not None:
            spec["expected_stdout"] = expected_stdout
        expected_commands.append(spec)
        return folder

    build_code = (
        "import subprocess,sys; roots="
        + repr([str(r) for r in roots.values()])
        + '; [subprocess.run([sys.executable,"-m","build","--no-isolation","--wheel","--sdist","--outdir",'
        + repr(str(wheelhouse))
        + ",r],check=True) for r in roots]"
    )
    command("wheel", [sys.executable, "-I", "-c", build_code])
    # sdist role independently checks every archive, then sdist->wheel executes.
    sdists = sorted(wheelhouse.glob("*.tar.gz"))
    command(
        "sdist",
        [
            sys.executable,
            "-I",
            "-c",
            "import tarfile; paths="
            + repr([str(p) for p in sdists])
            + "; [tarfile.open(p).getmembers() for p in paths]; assert len(paths)==8",
        ],
    )
    rebuilt = evidence / "sdist-rebuild"
    rebuilt.mkdir()
    rebuild_code = (
        "import pathlib,tarfile,subprocess,sys; base=pathlib.Path("
        + repr(str(rebuilt))
        + "); paths="
        + repr([str(p) for p in sdists])
        + '\nfor i,p in enumerate(paths):\n d=base/str(i);d.mkdir();tarfile.open(p).extractall(d,filter="data");src=next(d.iterdir());subprocess.run([sys.executable,"-m","build","--no-isolation","--wheel","--outdir",str(base/"wheels"),str(src)],check=True)'
    )
    command("sdist-wheel", [sys.executable, "-I", "-c", rebuild_code])
    build_commands, build_specs = list(commands), list(expected_commands)
    entries["packages"] = {version: {}}
    specs["packages"] = {version: {}}
    for kind in ("normal", "rebuilt"):
        start = len(commands)
        installed = (evidence if owner_namespaces else base) / ("installed-" + kind)
        command(
            "install", [sys.executable, "-I", "-m", "venv", "--without-pip", str(installed)]
        )
        py = str(installed / "bin/python")
        wheels = sorted((wheelhouse if kind == "normal" else rebuilt / "wheels").glob("*.whl"))
        command(
            "install",
            [
                sys.executable,
                "-I",
                "-m",
                "pip",
                "--python",
                py,
                "install",
                "--no-deps",
                "--no-index",
                *[str(p) for p in wheels],
            ],
        )
        probe_root = command(
            "probe", [py, "-I", "-m", "openpine.verification.installed_probe"]
        )
        package_probe = descriptor(evidence, probe_root / "stdout.log")
        module_list = list(modules.values())
        for role in ("compile", "run", "library"):
            code = (
                "import importlib,json; modules="
                + repr(module_list)
                + '; values=[importlib.import_module(m).add(2,2) for m in modules]; assert values==[4]*8; print(json.dumps({"values":values}))'
            )
            command(role, [py, "-I", "-c", code], expected_stdout={"values": [4] * 8})
        code = (
            "import importlib.resources,json; modules="
            + repr(module_list)
            + '; values=[json.loads(importlib.resources.files(m).joinpath("fixture-resource.json").read_text())["value"] for m in modules]; print(json.dumps({"resources":values}))'
        )
        command("resources", [py, "-I", "-c", code], expected_stdout={"resources": [4] * 8})
        for role, mode in (
            ("missing-resource", "missing"),
            ("tampered-resource", "tampered"),
        ):
            code = 'import importlib.resources,json; p=importlib.resources.files("openpine").joinpath("fixture-resource.json"); old=p.read_bytes(); '
            if mode == "missing":
                code += 'p.unlink()\ntry:\n p.read_bytes()\n raise AssertionError("missing resource accepted")\nexcept FileNotFoundError:\n print(json.dumps({"error":"missing resource"}))\nfinally:\n p.write_bytes(old)'
                expected = {"error": "missing resource"}
            else:
                code += 'p.write_text("{\\"value\\":5}")\ntry:\n assert json.loads(p.read_text())["value"] != 4\n print(json.dumps({"error":"tampered resource"}))\nfinally:\n p.write_bytes(old)'
                expected = {"error": "tampered resource"}
            command(role, [py, "-I", "-c", code], expected_stdout=expected)
        # Isolation ignores a poison cwd module; source path is not added to sys.path.
        poison = base / "poison"
        put(poison / "openpine.py", 'raise AssertionError("source shadowed wheel")\n')
        code = 'import openpine,json; assert openpine.add(2,2)==4; print(json.dumps({"value":openpine.add(2,2)}))'
        command(
            "source-shadowing",
            [py, "-I", "-c", code],
            cwd=poison,
            expected_stdout={"value": 4},
        )
        artifacts = {}
        for name in COMPONENTS:
            normalized = name.replace("-", "_")
            wheel = next(p for p in wheels if p.name.startswith(normalized + "-"))
            sdist = next(p for p in sdists if p.name.startswith(normalized + "-"))
            artifacts[name] = {
                "wheel": descriptor(evidence, wheel),
                "sdist": descriptor(evidence, sdist),
            }
        entries["packages"][version][kind] = {
            "commands": build_commands + commands[start:],
            "probe": package_probe,
            "artifacts": artifacts,
        }
        specs["packages"][version][kind] = {
            "python": version,
            "commands": build_specs + expected_commands[start:],
            "resources": {name: [modules[name] + "/fixture-resource.json"] for name in COMPONENTS},
        }
    # Real JS assertion/test result and actual build output, in isolated process.
    staged_ui = evidence / "frontend/source"
    frontend = staged_ui / "dist"
    javascript = (
        'const assert=require("node:assert/strict"),fs=require("node:fs");assert.equal(JSON.parse(fs.readFileSync("package.json")).name,"fixture-ui");assert.equal(2+2,4);fs.writeFileSync('
        + json.dumps(str(frontend / "bundle.js"))
        + ',"export const value=4;\\n");fs.writeFileSync(process.argv[1].split("=")[1],JSON.stringify({numFailedTests:0,numPassedTests:1,numTotalTests:1,testResults:[{assertionResults:[{fullName:"arithmetic",status:"passed"}]}]}));'
    )
    node = shutil.which("node")
    front_tests = evidence / "vitest.json"
    front_argv = [node, "-e", javascript, '--', '--outputFile.json=' + str(front_tests)]
    specs["frontend"] = {
        "commands": [{"role": role, "argv": front_argv, "cwd": str(staged_ui), "inputs": dict(harness_inputs)} for role in ("frontend-tests", "frontend-build")],
        "test_names": ["arithmetic"],
        "build_output_count": 1,
    }
    corpus_code = (
        "import json,hashlib,pathlib; root=pathlib.Path("
        + repr(str(corpus))
        + '); out={"status":"completed","compile":True,"events":[{"value":2+2}]}; out.update({key+"_sha256":hashlib.sha256((root/name).read_bytes()).hexdigest() for key,name in [("source","source.pine"),("data","data.json"),("settings","settings.json")]}); print(json.dumps(out))'
    )
    observation_root = command("corpus", [sys.executable, "-I", "-c", corpus_code])
    observation = read_json(observation_root / "stdout.log")
    # Timing targets here are intentionally near zero: this fixture tests the
    # positive reader, not an asserted product speedup or performance budget.
    from openpine.verification.execution_resources import resource_profile
    observed = resource_profile()
    limits = {k: observed[k] for k in ("cpu_quota", "memory_limit_bytes", "address_space_limit_bytes")}
    limits.update(jobs=min(4, observed["cpu_slots"]), max_parallel_shards=min(4, observed["cpu_slots"]), max_shards_per_task=1, cpu_frequency_max_khz=(observed["cpu_frequency_max_khz"][0] if observed["cpu_frequency_max_khz"] else None))
    specs["test-performance"] = {
        "organization_limits": {"before": limits, "after": limits},
        "reference_profile": {"affinity_cpus": len(os.sched_getaffinity(0))},
        "target_speedup": 0.00001,
    }
    policy = {
        "components": {
            name: {"dependencies": [], "pythons": [version], "timeout_seconds": 60}
            for name in COMPONENTS
        },
        "required_gates": {"stage-full": list(STABILIZATION_GATES)},
        "stabilization": specs,
    }
    product_entries = {}
    if product:
        from openpine.verification.stage_gate import product_requirements
        from openpine.verification.review_ledger import PYTHON_SUPPORT, SOURCE_SHA256
        domains = {}
        for gate, ids in product_requirements().items():
            # Real miniature command with independently authored expected4.
            # This is verifier self-check evidence, never production qualification.
            command_spec = {"argv": [sys.executable, "-I", "-c",
                "import json; print(json.dumps({'value':2+2}))"], "cwd": str(base),
                "inputs": dict(harness_inputs), "expected_stdout": {"value": 4}}
            domains[gate] = {"requirements": ids, "obligations": {
                requirement: {"nodes": {"openpine": nodes}, "commands": [command_spec]}
                for requirement in ids}}
        policy["product_acceptance"] = {"schema_id": "openpine.product_domain_policy.v1",
            "source_spec_sha256": SOURCE_SHA256, "python_support": PYTHON_SUPPORT, "domains": domains}
    launch = None
    if portable:
        from openpine.verification.execution_owner_launch import freeze_owner_launch
        import copy
        policy = copy.deepcopy(policy)
        policy['schema_id'] = 'openpine.execution_policy.v2'
        policy['owner_locator_slots'] = {'fixture': 'directory'}
        for owner in [policy['stabilization']['frontend'], *(lane for lanes in policy['stabilization']['packages'].values() for lane in lanes.values())]:
            for spec in owner['commands']:
                if isinstance(spec['cwd'], str):
                    relative = str(Path(spec['cwd']).relative_to(base))
                    spec['cwd'] = {'owner_locator': 'fixture', 'relative': '' if relative == '.' else relative}
        locations = {'fixture': str(base)}
        if owner_namespaces:
            policy['owner_locator_slots'].update(attempt='directory', package_attempt='directory')
            locations.update(attempt=str(evidence), package_attempt=str(evidence))
        launch = freeze_owner_launch(policy, locations)
    put(host / "verification/execution-policy.json", policy)
    # Remove backend build detritus before freezing actual source identity.
    for root in roots.values():
        for path in list(root.glob("*.egg-info")) + [root / "build"]:
            if path.exists():
                shutil.rmtree(path)
    source = source_snapshot(roots)
    env = freeze_fixture_environment(roots, evidence / "environment-probe", harness_inputs)
    inventories = {
        name
        + "@" + environment_name: {
            "nodeids": nodes,
            "reviewed_lock": lock,
            "deselected": 0,
            "source_hash": source["content_hash"],
            "environment_hash": env["content_hash"],
        }
        for name in COMPONENTS
    }
    plan = make_plan(
        profile="stage-full",
        policy=policy,
        roots=roots,
        source=source,
        inventories=inventories,
        environments={environment_name: {"identity": env, "executable": sys.executable}},
        shard_count=1,
        coverage=True,
        owner_launch=launch,
    )
    plan["source_commits"] = {name: "1" * 40 for name in roots}
    plan.pop("content_hash")
    plan = seal(plan)
    path = evidence / "plan.json"
    write_once_json(path, plan)
    shutil.copytree(host / "openpine-ui", staged_ui)
    frontend.mkdir()
    binding = {
        "plan_hash": plan["content_hash"], "candidate_hash": source["content_hash"],
        "source_root": str(staged_ui),
        "source_files": {n.removeprefix("openpine-ui/"): m for n, m in source["components"]["openpine"]["files"].items() if n.startswith("openpine-ui/")},
    }
    def front_artifacts():
        return {"tests": descriptor(evidence, front_tests), "outputs": [descriptor(evidence, frontend / "bundle.js")]}
    harness_inputs.update({"frontend-source:" + n: {"path": str(staged_ui / n), "sha256": m["sha256"]} for n, m in binding["source_files"].items()})
    front_start = len(commands)
    for role in ("frontend-tests", "frontend-build"):
        command(role, front_argv, cwd=staged_ui, binding=binding, artifacts=front_artifacts if role == "frontend-build" else None)
    entries["frontend"] = {"binding": binding, "commands": commands[front_start:], **front_artifacts()}
    from openpine.verification.execution_resources import resource_profile
    fixture_jobs = min(4, resource_profile()["cpu_slots"])
    run_campaign(plan, path, evidence / "run-0", jobs=fixture_jobs, run_id="fixture-0")
    timing_plan = make_plan(
        profile="stage-full", policy=policy, roots=roots, source=source,
        inventories=inventories,
        environments={environment_name: {"identity": env, "executable": sys.executable}},
        shard_count=1, coverage=False, owner_launch=launch,
    )
    timing_plan["source_commits"] = dict(plan["source_commits"])
    timing_plan.pop("content_hash")
    timing_plan = seal(timing_plan)
    timing_path = evidence / "timing-plan.json"
    write_once_json(timing_path, timing_plan)
    for index in range(10):
        run_campaign(
            timing_plan, timing_path, evidence / f"timing-{index}",
            jobs=fixture_jobs, run_id=f"fixture-timing-{index}",
        )
    entries["test-performance"] = {
        "before": [
            {
                "plan": descriptor(evidence, timing_path),
                "campaign": f"timing-{i}",
                "run_id": f"fixture-timing-{i}",
            }
            for i in range(5)
        ],
        "after": [
            {
                "plan": descriptor(evidence, timing_path),
                "campaign": f"timing-{i}",
                "run_id": f"fixture-timing-{i}",
            }
            for i in range(5, 10)
        ],
    }
    exported = evidence / "foundation"
    export_suite_receipts(
        plan,
        evidence / "run-0",
        exported,
        expected_plan_hash=plan["content_hash"],
        expected_run_id="fixture-0",
    )
    folder = exported / environment_name
    put(
        folder / "source-pins.json", read_json(host / "docs/RC6_LIFECYCLE_SOURCES.json")
    )
    put(folder / "observations/arithmetic.json", observation)
    run_stage_gate(host, stack, folder)
    entries["foundation"] = {"environments": {environment_name: "foundation/" + environment_name}}
    # The coverage owner must observe the same complete launch scope as pytest.
    coverage_code = (
        "import json; from pathlib import Path; "
        "from openpine.verification.execution_coverage import combine_task_coverage; "
        "from openpine.verification.identity import read_json; "
        f"evidence=Path({str(evidence)!r}); plan=read_json(Path({str(path)!r})); "
        "results={t['id']:combine_task_coverage(plan,evidence/'run-0',t['id'],"
        "evidence/'coverage'/t['id'],run_id='fixture-0') for t in plan['tasks']}; "
        "assert all(r['ok'] for r in results.values()),results; "
        "print(json.dumps(results))"
    )
    coverage_owner = run_logged(
        [sys.executable, "-B", "-c", coverage_code],
        cwd=host,
        output=evidence / "coverage-owner",
        env=clean_environment(
            {n: str(r) for n, r in roots.items()}, evidence / "coverage-private"
        ),
        inputs={
            **harness_inputs,
            "plan": {"path": str(path), "sha256": hash_file(path)},
        },
        timeout=180,
    )
    assert coverage_owner["ok"], coverage_owner
    coverage_entries = {t["id"]: "coverage/" + t["id"] for t in plan["tasks"]}
    entries["coverage"] = {"tasks": coverage_entries}
    packet = {
        "schema_id": "openpine.rc6_stabilization_inputs.v1",
        "plan_hash": plan["content_hash"],
        "candidate_hash": source["content_hash"],
        "run_id": "fixture-0",
        "campaign": "run-0",
        "gates": entries,
    }
    put(evidence / "stabilization-inputs.json", packet)
    if product:
        for gate, domain in policy["product_acceptance"]["domains"].items():
            command_spec = next(iter(domain["obligations"].values()))["commands"][0]
            folder = evidence / ("product-" + gate)
            actual = run_logged(command_spec["argv"], cwd=base, output=folder, env=clean,
                inputs=command_spec["inputs"], binding={"plan_hash": plan["content_hash"],
                "candidate_hash": source["content_hash"], "run_id": "fixture-0"}, timeout=60)
            assert actual["ok"], actual
            product_entries[gate] = {requirement: [descriptor(evidence, folder / "command.json")]
                                    for requirement in domain["requirements"]}
        put(evidence / "product-inputs.json", {"schema_id": "openpine.product_inputs.v1",
            "plan_hash": plan["content_hash"], "candidate_hash": source["content_hash"],
            "policy_hash": plan["policy_hash"], "run_id": "fixture-0", "domains": product_entries})
    return host, plan, evidence, packet
