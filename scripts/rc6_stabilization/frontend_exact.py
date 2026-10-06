"""Pinned frontend command declaration and exact primary execution."""

import json
import os
import sys
import shutil
from pathlib import Path

OUT = Path(os.environ["OPENPINE_ATTEMPT_ROOT"])
HOST = OUT / "build-copies/openpine"
BASE = OUT / "frontend"
UI = BASE / "source"
NPM = os.environ.get("OPENPINE_NPM") or shutil.which("npm")
PY = os.environ.get("OPENPINE_PYTHON") or sys.executable
PROXY = os.environ.get("OPENPINE_PROXY")


def specifications(*, attempt_root=None, host_root=None, npm=None, python=None, proxy=None):
    OUT = Path(attempt_root) if attempt_root is not None else globals()["OUT"]
    HOST = Path(host_root) if host_root is not None else OUT / "build-copies/openpine"
    BASE = OUT / "frontend"
    UI = BASE / "source"
    NPM = npm or globals()["NPM"]
    PY = python or globals()["PY"]
    if not NPM or not Path(NPM).is_absolute() or not Path(PY).is_absolute():
        raise ValueError("frontend tools require explicit absolute executable locations")
    proxy_arguments = [] if proxy is None else ["--proxy=" + proxy, "--https-proxy=" + proxy]
    return [
        {
            "role": "frontend-openapi",
            "argv": [PY, "-B", str(HOST / "scripts/export_openapi.py"), str(BASE / "openapi.json")],
            "cwd": str(HOST),
        },
        {"role": "frontend-install", "argv": [NPM, "ci", *proxy_arguments], "cwd": str(UI)},
        {
            "role": "frontend-tests",
            "argv": [
                NPM,
                "run",
                "test:coverage",
                "--",
                "--reporter=default",
                "--reporter=json",
                "--outputFile.json=" + str(BASE / "vitest.json"),
            ],
            "cwd": str(UI),
        },
        {"role": "frontend-build", "argv": [NPM, "run", "build"], "cwd": str(UI)},
        {"role": "frontend-node", "argv": [NPM, "run", "test:node"], "cwd": str(UI)},
        {
            "role": "frontend-client-generation",
            "argv": [
                PY,
                "-B",
                str(HOST / "scripts/generate_openapi_ts.py"),
                str(BASE / "openapi.json"),
                str(BASE / "openapi.ts"),
            ],
            "cwd": str(UI),
        },
        {
            "role": "frontend-client-drift",
            "argv": [
                PY,
                "-I",
                "-c",
                "from pathlib import Path; import sys; assert Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()",
                str(BASE / "openapi.ts"),
                str(UI / "src/api/generated/openapi.ts"),
            ],
            "cwd": str(UI),
        },
        {
            "role": "frontend-e2e",
            "argv": [
                NPM,
                "run",
                "test:e2e",
                "--",
                "--workers=1",
                "--reporter=json",
                "--output=" + str(BASE / "test-results"),
            ],
            "cwd": str(UI),
        },
        {
            "role": "frontend-audit",
            "argv": [NPM, "audit", "--audit-level=moderate", "--json", *proxy_arguments],
            "cwd": str(UI),
        },
    ]


def prepare_attempt(roots):
    from scripts.rc6_stabilization.harness import validate_attempt_output

    validate_attempt_output(OUT, roots)
    OUT.mkdir(parents=True, exist_ok=False)
    BASE.mkdir(exist_ok=False)


def main():
    plan = json.loads(Path(os.environ["OPENPINE_PLAN"]).read_text())
    from openpine.verification.execution_binding import checked_locations

    checked_binding = (
        json.loads(Path(os.environ["OPENPINE_BINDING"]).read_text())
        if os.environ.get("OPENPINE_BINDING")
        else None
    )
    roots, _ = checked_locations(plan, checked_binding)
    HOST = Path(plan.get("owner_launch", {}).get("paths", {}).get("host", str(globals()["HOST"])))
    from openpine.verification.execution_owner_launch import _path
    from openpine.verification.execution_identity import source_snapshot

    _path(str(HOST), live=True)
    # The checked roots may be relocated independently of the generator staging
    # checkout. Bind the checkout actually imported/executed, not just its UI or
    # policy, while admitting byte-identical copies of the frozen component.
    if source_snapshot({"openpine": HOST})["components"]["openpine"] != plan["source"][
        "components"
    ].get("openpine"):
        raise ValueError("frontend host is not the frozen OpenPine component")
    sys.path.insert(0, str(HOST))
    from openpine.verification.execution_process import run_logged
    from openpine.verification.execution_campaign import descriptor
    from openpine.verification.stabilization_evidence import verify_frontend
    from openpine.verification.execution_identity import write_once_json
    from openpine.verification.execution_owner_launch import resolve_producer_policy

    reviewed = resolve_producer_policy(
        plan,
        json.loads((HOST / "verification/execution-policy.json").read_text()),
        attempt=OUT,
        helpers=Path(__file__).resolve().parent,
        owner="frontend",
    )
    npm, python = NPM, PY
    if reviewed.get("schema_id") == "openpine.execution_policy.v2":
        npm, python = (
            plan["owner_launch"]["paths"]["npm"],
            plan["owner_launch"]["paths"]["python313"],
        )
    policy = reviewed["stabilization"]["frontend"]
    assert [
        {k: v for k, v in spec.items() if k not in {"inputs", "owner_extra_tools"}}
        for spec in policy["commands"]
    ] == specifications(host_root=HOST, npm=npm, python=python, proxy=PROXY)
    files = {
        n.removeprefix("openpine-ui/"): m
        for n, m in plan["source"]["components"]["openpine"]["files"].items()
        if n.startswith("openpine-ui/")
    }
    binding = {
        "plan_hash": plan["content_hash"],
        "candidate_hash": plan["source"]["content_hash"],
        "source_root": str(UI),
        "source_files": files,
    }
    frozen = reviewed["stabilization"]["package_harness_inputs"]
    inputs = {
        **frozen,
        **{
            "frontend-source:" + n: {"path": str(UI / n), "sha256": m["sha256"]}
            for n, m in files.items()
        },
    }
    prepare_attempt({**roots, "frontend-host": HOST})
    shutil.copytree(
        HOST / "openpine-ui",
        UI,
        ignore=shutil.ignore_patterns(
            "node_modules", "dist", "coverage", "test-results", "playwright-report", "*.tsbuildinfo"
        ),
    )
    private = BASE / "private"
    private.mkdir()
    for name in ["home", "cache"]:
        (private / name).mkdir()
    tmp = Path(os.environ["TMPDIR"]) / ("rc6-ui-" + OUT.name)
    tmp.mkdir(exist_ok=False)
    env = {k: v for k, v in os.environ.items() if k in ["PATH", "LANG", "LC_ALL", "TZ"]}
    env.update(
        HOME=str(private / "home"),
        TMPDIR=str(tmp),
        XDG_CACHE_HOME=str(private / "cache"),
        npm_config_cache=str(private / "cache/npm"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(HOST),
        OPENPINE_OPENAPI_SCHEMA=str(BASE / "openapi.json"),
        NO_PROXY="localhost,127.0.0.1,::1",
        NODE_OPTIONS="--max-old-space-size=1024",
    )
    chromium = plan.get("owner_launch", {}).get("paths", {}).get("chromium") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"
    )
    if chromium:
        env["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"] = chromium
    if PROXY:
        env.update(HTTPS_PROXY=PROXY, HTTP_PROXY=PROXY)
    node = plan.get("owner_launch", {}).get("paths", {}).get("node")
    if node:
        if Path(node).name != "node":
            raise ValueError("frontend Node executable must provide the declared node name")
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    commands = []
    for index, spec in enumerate(policy["commands"]):
        folder = BASE / "commands" / str(index)

        def primary_artifacts():
            return {
                "tests": descriptor(OUT, BASE / "vitest.json"),
                "outputs": [
                    descriptor(OUT, p) for p in sorted((UI / "dist").rglob("*")) if p.is_file()
                ],
            }

        r = run_logged(
            spec["argv"],
            cwd=Path(spec["cwd"]),
            output=folder,
            env=env,
            timeout=1200,
            inputs={**inputs, **spec.get("inputs", {})},
            binding=binding,
            artifacts=primary_artifacts if index == len(policy["commands"]) - 1 else None,
        )
        commands.append(descriptor(OUT, folder / "command.json"))
        write_once_json(BASE / f"command-{index}-descriptor.json", commands[-1])
        print("FRONTEND", spec["role"], r["returncode"], flush=True)
        assert r["ok"], (folder / "stdout.log").read_text() + (folder / "stderr.log").read_text()
    entry = {
        "binding": binding,
        "commands": commands,
        "tests": descriptor(OUT, BASE / "vitest.json"),
        "outputs": [descriptor(OUT, p) for p in sorted((UI / "dist").rglob("*")) if p.is_file()],
    }
    policy["commands"] = [
        {**s, "inputs": {**frozen, **s.get("inputs", {})}} for s in policy["commands"]
    ]
    result = verify_frontend(plan, OUT, entry, policy)
    write_once_json(BASE / "entry.json", entry)
    write_once_json(BASE / "owner-readback.json", result)
    print("FRONTEND_OWNER_READBACK", json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
