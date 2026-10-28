"""Read-only diagnosis, paged compatibility and explicit replacement runs."""
import argparse
import sys
from pathlib import Path

import apsw

from packages.contracts.wire import canonical, atomic_json
from apps.openpine.diagnostics import doctor, compatibility, diagnostic_package, KINDS


def main(command, argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=Path('.runtime/jobs'))
    parser.add_argument('--output',type=Path)
    if command=='doctor':parser.add_argument('--full',action='store_true')
    elif command=='compatibility':
        parser.add_argument('--kind',choices=KINDS,default='jobs')
        parser.add_argument('--start',type=int,default=0);parser.add_argument('--limit',type=int,default=50)
    elif command=='diagnostics':parser.add_argument('--max-bytes',type=int,default=1024**2)
    elif command=='restart':
        parser.add_argument('job_id')
        parser.add_argument('--preview-id');parser.add_argument('--request-id')
    args=parser.parse_args(argv)
    # Reports must not overwrite any member of the inspected store.
    if args.output and args.output.resolve().is_relative_to(args.state_dir.resolve()):
        parser.error('--output must be outside the store.')
    try:
        if command=='doctor': value=doctor(args.state_dir,full=args.full)
        elif command=='compatibility':value=compatibility(args.state_dir,kind=args.kind,start=args.start,limit=args.limit)
        elif command=='diagnostics':value=diagnostic_package(args.state_dir,max_bytes=args.max_bytes)
        else:
            from apps.openpine.bootstrap import local_manager
            from apps.openpine.restarts import preview, apply
            if bool(args.preview_id)!=bool(args.request_id):parser.error('--preview-id and --request-id must be supplied together.')
            with local_manager(args.state_dir,autostart=False) as manager:
                value=apply(manager,args.job_id,preview_id=args.preview_id,request_id=args.request_id) if args.preview_id else preview(manager,args.job_id)
        code=2 if value.get('status') in ('attention','schema_upgrade_required') else 0
    except (ValueError,OSError,KeyError,apsw.Error) as error:
        value={'status':'rejected','diagnostics':[{'code':'MAINTENANCE_FAILED','message':str(error)}]};code=2
    if args.output:atomic_json(args.output,value)
    else:sys.stdout.write(canonical(value).decode('utf-8')+'\n')
    return code
