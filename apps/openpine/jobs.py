"""Run an isolated job through the shared supervisor."""

import argparse
import sys
import time
from pathlib import Path

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.wire import atomic_json, canonical, read_json
from packages.execution.artifacts import export_program, read_program
from packages.execution.jobs import JobManager
from packages.execution.service import rejection
from packages.marketdata import from_payload
from packages.marketdata.providers import make_snapshot, snapshot_from_payload, snapshot_payload


def input_snapshot(payload):
    if type(payload) is dict and payload.get("schema_version") == "openpine.market-snapshot.1":
        return snapshot_from_payload(payload)
    return make_snapshot(from_payload(payload), {"provider": "uploaded-file.1", "gap_policy": "preserve"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--artifact", type=Path)
    parser.add_argument("--bars", type=Path, required=True, help="historical bars or saved market snapshot")
    parser.add_argument("--state-dir", type=Path, default=Path(".runtime/jobs"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() in {p.resolve() for p in (args.source, args.artifact, args.bars) if p}:
        parser.error("--output must not overwrite an input")
    try:
        snapshot = input_snapshot(read_json(args.bars))
        if args.source:
            with args.source.open("rb") as stream:
                text = stream.read(65537).decode("utf-8")
            content = {"source": text, "source_name": args.source.name}
        else:
            content = {"artifact": export_program(read_program(args.artifact))}
        request = {"schema_version": "openpine.job-request.1", "input": content,
                   "snapshot": snapshot_payload(snapshot), "timeout_seconds": args.timeout}
        with JobManager(args.state_dir) as manager:
            state = manager.submit(request)
            try:
                while state["status"] not in TERMINAL_STATUSES:
                    time.sleep(.05)
                    state = manager.get(state["job_id"])
            except KeyboardInterrupt:
                state = manager.cancel(state["job_id"])
            result = manager.result(state["job_id"]) if state["result_sha256"] else None
            payload = {"job": state, "response": result}
        code = 0 if state["status"] == "succeeded" else 2
    except (ValueError, OSError, UnicodeError) as error:
        try:
            payload = rejection(error)
        except ValueError:
            payload = {"status": "rejected", "diagnostics": [{"code": "INPUT_INVALID", "message": str(error)}]}
        code = 2
    if args.output:
        atomic_json(args.output, payload)
    else:
        sys.stdout.write(canonical(payload).decode("utf-8") + "\n")
    return code
