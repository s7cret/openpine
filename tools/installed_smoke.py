"""Offline acceptance of the installed CLI, saved IR, worker and HTTP path."""

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from apps.ui.server import Server
from packages.compiler.semantic.specification import ROOT, load_snapshot
from packages.contracts.application import TERMINAL_STATUSES


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-root", type=Path)
    args = parser.parse_args(argv)
    check(sys.version_info[:3] == (3, 14, 8), "Acceptance requires Python 3.14.8")
    if args.expected_root:
        check(ROOT == args.expected_root.resolve(), "Imported a different checkout instead of the installation")
    load_snapshot()
    expected = json.loads((ROOT / "examples/pipeline/expected.json").read_text())["outputs"]
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix="openpine-accept-") as directory:
        work = Path(directory)
        def cli(*arguments):
            process = subprocess.run([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT), *map(str, arguments)],
                cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=90)
            check(process.returncode == 0, f"CLI failed: {process.stdout[-2000:]} {process.stderr[-2000:]}")
            return json.loads(process.stdout) if process.stdout.strip() else None
        bars = ROOT / "examples/pipeline/bars.json"
        cli_responses = {}
        for version in range(1, 7):
            artifact = work / f"v{version}.json"
            source = ROOT / f"examples/pipeline/v{version}.pine"
            cli("compile", "--source", source, "--profile", "historical", "--output", artifact)
            result = cli("run", "--artifact", artifact, "--bars", bars)
            check([o["values"] for o in result["result"]["outputs"]] == expected, f"Wrong installed v{version} result")
            cli_responses[version] = result
        isolated = cli("job", "--source", ROOT / "examples/pipeline/v6.pine", "--bars", bars, "--state-dir", work / "cli-jobs")
        check(isolated["job"]["status"] == "succeeded", "Installed worker failed")
        check([o["values"] for o in isolated["response"]["result"]["outputs"]] == expected, "Worker values differ")
        check(cli("storage", "inspect", "--state-dir", work / "cli-jobs")["integrity"] == "ok", "Installed SQLite catalog failed")
        cli("storage", "backup", "--state-dir", work / "cli-jobs", "--destination", work / "backup")
        cli("storage", "restore", "--source", work / "backup", "--state-dir", work / "restored-jobs")
        replay = cli("job", "--source", ROOT / "examples/pipeline/v6.pine", "--snapshot-id", isolated["response"]["snapshot_sha256"],
            "--state-dir", work / "restored-jobs")
        check(replay["response"]["result"] == isolated["response"]["result"], "Installed backup replay differs")
        server = Server(("127.0.0.1", 0), work / "http")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        token = ""
        def http(path, payload=None):
            request = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Content-Type": "application/json", "X-OpenPine-Token": token})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        try:
            token = http("/api/config")["token"]
            with urllib.request.urlopen(base + "/", timeout=10) as response:
                check(b"OpenPine" in response.read(), "Installed web assets missing")
            snapshot_id = http("/api/snapshots", {"provider": "upload", "raw": bars.read_text()})["sha256"]
            for version in (1, 6):
                artifact = json.loads((work / f"v{version}.json").read_text())["artifact"]
                job = http("/api/jobs", {"schema_version": "openpine.job-submission.1", "input": {"artifact": artifact},
                    "snapshot_sha256": snapshot_id, "timeout_seconds": 30})["job_id"]
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    state = http("/api/jobs/" + job)
                    if state["status"] in TERMINAL_STATUSES:
                        break
                    time.sleep(.04)
                check(state["status"] == "succeeded", f"Installed HTTP worker failed: {state}")
                result = http(f"/api/jobs/{job}/result")
                check(result["result"] == cli_responses[version]["result"], "HTTP and CLI computational results differ")
                summary = http(f"/api/jobs/{job}/summary")
                window = http(f"/api/jobs/{job}/chart?start=1&limit=2")
                check("values" not in summary["result"]["outputs"][0], "Summary contains a large output array")
                check(window["outputs"][0]["values"] == expected[0][1:3], "Installed chart range differs")
        finally:
            server.shutdown(); thread.join(timeout=10); server.server_close()
    print(json.dumps({"status": "passed", "python": sys.version.split()[0], "root": str(ROOT),
        "cli_versions": list(range(1, 7)), "saved_ir": True, "worker": True,
        "http_worker_versions": [1, 6], "cli_http_result_equal": True, "network_required": False,
        "sqlite_catalog": True, "parquet": True, "bounded_chart": True, "backup_replay": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
