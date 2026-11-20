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
    parser.add_argument("--plan-only",action="store_true",help="show the immutable run plan without submitting a job")
    parser.add_argument("--fx-rates",type=Path,help="pinned daily currency rates")
    parser.add_argument("--corporate-actions",type=Path,help="pinned offline corporate action dataset")
    parser.add_argument("--contexts",type=Path,help="explicit context inputs file; additional bars/snapshot IDs")
    parser.add_argument('--broker-settings',type=Path,help='explicit broker settings JSON, including tick size for nonzero slippage')
    parser.add_argument('--magnifier',type=Path,help='versioned lower-snapshot reconstruction configuration JSON')
    parser.add_argument('--lower-bars',type=Path,help='save lower candles and bind their snapshot to --magnifier')
    parser.add_argument('--inputs',type=Path,help='JSON object of input overrides; new jobs only')
    parser.add_argument('--execution-time-ms', type=int, help='frozen Unix milliseconds for historical timenow; new jobs only')
    parser.add_argument('--visible-range', nargs=2, type=int, metavar=('LEFT_MS', 'RIGHT_MS'),
                        help='inclusive visible primary candle openings in Unix milliseconds; new jobs only')
    parser.add_argument("--checkpoint-every",type=int,default=100000,help="new jobs: completed bars between checkpoints; 0 disables periodic saves")
    parser.add_argument("--pause-after",type=int,help="new jobs: checkpoint and pause after this absolute bar count")
    arguments(parser)
    args = parser.parse_args(argv)
    if bool(args.resume) == bool(args.bars or args.snapshot_id):
        parser.error("New jobs require --bars/--snapshot-id; --resume uses its saved input.")
    if args.resume and args.pause_after is not None:parser.error("--pause-after applies to new jobs")
    if args.resume and args.contexts:parser.error("--resume uses its saved context inputs")
    if args.resume and args.broker_settings:parser.error('--resume uses its pinned broker settings')
    if args.resume and (args.magnifier or args.lower_bars):parser.error('--resume uses its pinned magnifier input')
    if args.lower_bars and not args.magnifier:parser.error('--lower-bars requires --magnifier')
    if args.resume and args.inputs:parser.error('--resume uses its pinned input settings')
    if args.resume and args.execution_time_ms is not None:parser.error('--resume uses its pinned execution time')
    if args.plan_only and args.resume:parser.error("--plan-only applies to new jobs")
    if args.resume and args.fx_rates:parser.error("--resume uses its pinned FX actions")
    if args.resume and args.corporate_actions:parser.error("--resume uses its pinned corporate actions")
    if args.resume and args.visible_range is not None:parser.error('--resume uses its pinned visible range')
    if args.output and args.output.resolve() in {p.resolve() for p in (args.source, args.artifact, args.bars,args.broker_settings,args.inputs,args.contexts,args.magnifier,args.lower_bars) if p}:
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
        with JobManager(args.state_dir, autostart=not args.plan_only, policy=policy(args), coordinator_policy=coordinator_policy(args)) as manager:
            if args.resume:state=manager.resume(args.resume)
            else:
                request = {"schema_version": "openpine.job-request.2", "input_ref": manager.freeze_input(content),
                    "snapshot_id": manager.market.save(snapshot) if snapshot else args.snapshot_id,
                    "timeout_seconds": args.timeout}
                if args.corporate_actions:
                    if args.output and args.output.resolve()==args.corporate_actions.resolve():parser.error("Output overlaps corporate input.")
                    request['corporate_snapshot_id']=manager.save_corporate_actions(read_json(args.corporate_actions))
                if args.fx_rates:
                    if args.output and args.output.resolve()==args.fx_rates.resolve():parser.error("Output overlaps FX input.")
                    request['fx_snapshot_id']=manager.save_fx_rates(read_json(args.fx_rates))
                if args.contexts:
                    from apps.openpine.contexts import load_context_inputs
                    request.update(load_context_inputs(manager,args.contexts,args.output));request["schema_version"]="openpine.job-request.4"
                if args.broker_settings:
                    from packages.contracts.trading import BrokerSettings
                    settings=read_json(args.broker_settings);BrokerSettings.from_payload(settings)
                    request.update(schema_version='openpine.job-request.5',broker_settings=settings,
                        primary_context=request.get('primary_context'),contexts=request.get('contexts',[]))
                if args.inputs:request.update(schema_version='openpine.job-request.6',inputs=read_json(args.inputs))
                if args.magnifier:
                    selected=read_json(args.magnifier)
                    if args.lower_bars:selected={**selected,'snapshot_id':manager.market.save(input_snapshot(read_json(args.lower_bars)))}
                    request.update(schema_version='openpine.job-request.8',magnifier=selected)
                    request.setdefault('inputs',{})
                if args.execution_time_ms is not None:request['execution_time_ms']=args.execution_time_ms
                if args.visible_range is not None:
                    request['visible_range'] = dict(zip(('left_ms', 'right_ms'), args.visible_range, strict=True))
                if args.plan_only:
                    from dataclasses import replace
                    from packages.contracts.corporate import requirements
                    from packages.contracts.contexts import MarketContext
                    from packages.contracts.calendar import next_open
                    frozen = manager.preparation.freeze(request)
                    plan = replace(manager.preparation.build_plan(frozen,manager.policy),checkpoint_interval_bars=args.checkpoint_every)
                    preview = {'plan':plan.payload()}
                    if plan.corporate_snapshot_id or plan.fx_snapshot_id:
                        data = manager.market.manifest(plan.snapshot_id)['identity']
                        primary = MarketContext(data['context_id'],data['symbol'],data['timeframe'],'plan')
                        program = manager.preparation.program(manager.objects.get(plan.input_ref))
                    if plan.corporate_snapshot_id:
                        preview['corporate_actions'] = [{**r,'start_ms':data['first_ms'],
                            'end_ms':next_open(data['last_ms'],data['timeframe']),'snapshot_id':plan.corporate_snapshot_id}
                            for r in requirements(program,plan.settings,primary)]
                    if plan.fx_snapshot_id:
                        from packages.execution.corporate import planned_rates
                        from packages.contracts.corporate import ActionIndex
                        actions = ActionIndex(manager.objects.get(plan.corporate_snapshot_id)) if plan.corporate_snapshot_id else None
                        currency = manager.market.manifest(plan.snapshot_id)['provenance'].get('environment',{}).get('instrument',{}).get('currency')
                        preview['fx_rates'] = planned_rates(program,plan.settings,primary,actions,currency,plan.fx_snapshot_id,
                                                            data['first_ms'],next_open(data['last_ms'],data['timeframe']),plan.symbol_resolutions)
                    if args.output:atomic_json(args.output,preview)
                    else:sys.stdout.write(canonical(preview).decode('utf-8') + "\n")
                    return 0
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
