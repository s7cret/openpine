"""Create and resume independent temporal studies, locally or through the server."""
import argparse
from pathlib import Path
import sys
import time
import apsw
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from apps.openpine.resources import arguments, policy, coordinator_policy
from packages.contracts import OpenPineError
from packages.contracts.wire import canonical, read_json, atomic_json
from packages.marketdata.providers import input_snapshot


def main(argv=None):
    from apps.openpine.http_client import research_options,client_rejection
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create','run','status','list','pause','resume','cancel','report','delete-preview','delete-apply'))
    parser.add_argument('--id');parser.add_argument('--request', type=Path)
    parser.add_argument('--source', type=Path);parser.add_argument('--bars', type=Path)
    parser.add_argument('--state-dir', type=Path, default=Path('.runtime/jobs'))
    parser.add_argument('--server');parser.add_argument('--preview-id')
    parser.add_argument('--start', type=int, default=0);parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--output', type=Path)
    research_options(parser);arguments(parser);args = parser.parse_args(argv)
    if args.action == 'create' and (not args.request or args.id):parser.error('create requires --request and no --id')
    if args.action == 'run' and bool(args.id) == bool(args.request):parser.error('run requires --id OR --request')
    if args.action not in ('create','run') and (args.request or args.source or args.bars):parser.error('Only new studies accept source/data/request.')
    if args.action not in ('create','run','list') and not args.id:parser.error('This action requires --id')
    if (args.source or args.bars) and not args.request:parser.error('Source/data require --request')
    if args.output and args.output.resolve() in {p.resolve() for p in (args.request,args.source,args.bars) if p}:parser.error('Output must not overwrite inputs.')
    if (args.request_id or args.operation_file or args.new_request) and not args.request:parser.error('Submission options require a new --request.')
    if (args.operation_file or args.new_request) and not args.server:parser.error('Client operation files require --server.')
    code = 0
    try:
        if args.server:
            from apps.openpine.http_client import research_command
            result,code = research_command(args, 'walkforward')
        else:
            with local_manager(args.state_dir, policy=policy(args), coordinator_policy=coordinator_policy(args)) as manager:
                experiments = Experiments(manager, start=False)
                searches = Searches(manager, experiments, start=False)
                studies = WalkForwards(manager, searches, start=False)
                try:
                    identifier = args.id
                    if args.request:
                        payload = read_json(args.request)
                        if args.source:payload['input_ref'] = manager.freeze_input({'source':args.source.read_text(encoding='utf-8'),'source_name':args.source.name})
                        if args.bars:payload['snapshot_id'] = manager.market.save(input_snapshot(read_json(args.bars)))
                        result = studies.create(payload,request_id=args.request_id);identifier = result['walkforward_id']
                    if args.action == 'run':
                        studies.control(identifier, 'resume')
                        try:
                            while True:
                                studies.tick();searches.tick();experiments.tick()
                                result = studies.status(identifier, start=args.start, limit=args.limit)
                                if result['status'] != 'running':break
                                time.sleep(.1)
                        except KeyboardInterrupt:result = studies.control(identifier, 'pause')
                        code = 0 if result['status'] in ('succeeded','paused') else 2
                    elif args.action == 'status':result = studies.status(identifier, start=args.start, limit=args.limit)
                    elif args.action == 'list':result = studies.list()
                    elif args.action in ('pause','resume','cancel'):result = studies.control(identifier, args.action)
                    elif args.action == 'report':
                        from apps.openpine.research_reports import ResearchReports
                        result = ResearchReports(manager).create('walkforward', identifier)
                    elif args.action.startswith('delete-'):
                        from apps.openpine.research_maintenance import ResearchMaintenance
                        maintenance = ResearchMaintenance(manager)
                        result = maintenance.preview('walkforward', identifier) if args.action == 'delete-preview' else maintenance.apply('walkforward', identifier, args.preview_id)
                finally:studies.close();searches.close();experiments.close()
    except (OpenPineError,ValueError,TypeError,KeyError,OSError,apsw.Error) as error:
        result=client_rejection(error,'WALKFORWARD_INVALID');code=2
    if args.output:atomic_json(args.output, result)
    else:sys.stdout.write(canonical(result).decode('utf-8')+'\n')
    return code
