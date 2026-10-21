"""Run an isolated job through the shared supervisor."""

import argparse
import sys
import time
import apsw
from pathlib import Path

from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.wire import atomic_json, canonical, read_json
from packages.execution.artifacts import export_program, read_program
from apps.openpine.bootstrap import local_manager as JobManager
from packages.execution.service import rejection
from packages.marketdata.providers import input_snapshot, snapshot_payload
from apps.openpine.resources import arguments, policy, coordinator_policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--artifact", type=Path)
    source.add_argument("--resume", help="resume a paused job in this local state directory")
    data = parser.add_mutually_exclusive_group()
    data.add_argument("--bars", type=Path, help="historical bars or saved market snapshot")
    data.add_argument("--snapshot-id", help="immutable snapshot already in the state directory")
    parser.add_argument("--state-dir", type=Path, default=Path(".runtime/jobs"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contexts",type=Path,help="explicit context inputs file; additional bars/snapshot IDs")
    parser.add_argument('--broker-settings',type=Path,help='explicit broker settings JSON, including tick size for nonzero slippage')
    parser.add_argument("--checkpoint-every",type=int,default=100000,help="new jobs: completed bars between checkpoints; 0 disables periodic saves")
    parser.add_argument("--pause-after",type=int,help="new jobs: checkpoint and pause after this absolute bar count")
    arguments(parser)
    args = parser.parse_args(argv)
    if bool(args.resume) == bool(args.bars or args.snapshot_id):
        parser.error("New jobs require --bars/--snapshot-id; --resume uses its saved input.")
    if args.resume and args.pause_after is not None:parser.error("--pause-after applies to new jobs")
    if args.resume and args.contexts:parser.error("--resume uses its saved context inputs")
    if args.resume and args.broker_settings:parser.error('--resume uses its pinned broker settings')
    if args.output and args.output.resolve() in {p.resolve() for p in (args.source, args.artifact, args.bars,args.broker_settings) if p}:
        parser.error("--output must not overwrite an input")
    try:
        snapshot = input_snapshot(read_json(args.bars)) if args.bars else None
        if args.resume:
            content=None
        elif args.source:
            with args.source.open("rb") as stream:
                text = stream.read(65537).decode("utf-8")
            content = {"source": text, "source_name": args.source.name}
        else:
            content = {"artifact": export_program(read_program(args.artifact))}
        with JobManager(args.state_dir, policy=policy(args), coordinator_policy=coordinator_policy(args)) as manager:
            if args.resume:state=manager.resume(args.resume)
            else:
                request = {"schema_version": "openpine.job-request.2", "input_ref": manager.freeze_input(content),
                    "snapshot_id": manager.market.save(snapshot) if snapshot else args.snapshot_id,
                    "timeout_seconds": args.timeout}
                if args.contexts:
                    from apps.openpine.contexts import load_context_inputs
                    request.update(load_context_inputs(manager,args.contexts,args.output));request["schema_version"]="openpine.job-request.4"
                if args.broker_settings:
                    from packages.contracts.trading import BrokerSettings
                    settings=read_json(args.broker_settings);BrokerSettings.from_payload(settings)
                    request.update(schema_version='openpine.job-request.5',broker_settings=settings,
                        primary_context=request.get('primary_context'),contexts=request.get('contexts',[]))
                state = manager.submit(request,checkpoint_interval_bars=args.checkpoint_every,pause_after=args.pause_after)
            try:
                while state["status"] not in TERMINAL_STATUSES and state["status"]!="paused":
                    time.sleep(.05)
                    state = manager.get(state["job_id"])
            except KeyboardInterrupt:
                state = manager.cancel(state["job_id"])
            result = manager.result(state["job_id"]) if state["result_sha256"] else None
            payload = {"job": state, "response": result}
        code = 0 if state["status"] in ("succeeded","paused") else 2
    except (ValueError, OSError, UnicodeError, apsw.Error) as error:
        try:
            payload = rejection(error)
        except (ValueError, apsw.Error):
            payload = {"status": "rejected", "diagnostics": [{"code": "INPUT_INVALID", "message": str(error)}]}
        code = 2
    if args.output:
        atomic_json(args.output, payload)
    else:
        sys.stdout.write(canonical(payload).decode("utf-8") + "\n")
    return code
