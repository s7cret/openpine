"""Keyed online writes and read-only offline access to pinned comparisons and retention."""
import argparse,sys,time,uuid
from pathlib import Path
from packages.contracts.wire import canonical,atomic_json
from apps.openpine.http_client import LocalClient

def main(group,argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    commands=('run','list','status','explanation','trace','pause','resume','cancel') if group=='compare' else ('list','policy','set-policy','candidates','delete-preview','delete-apply')
    p.add_argument('command',choices=commands);p.add_argument('--server');p.add_argument('--state-dir',type=Path,default=Path('.runtime/jobs'))
    p.add_argument('--id');p.add_argument('--left');p.add_argument('--right');p.add_argument('--mode',choices=('compatible','data_only'),default='compatible')
    p.add_argument('--kind',choices=('trace','comparison'),default='trace');p.add_argument('--side',type=int,default=0)
    p.add_argument('--start',type=int,default=0);p.add_argument('--limit',type=int,default=50);p.add_argument('--keep-days',type=int)
    p.add_argument('--preview-id');p.add_argument('--wait',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args(argv)
    reads=('list','status','explanation','policy','candidates')
    if not a.server and a.command not in reads:p.error('Use --server for submissions and controls; offline mode only reads.')
    if a.command in ('status','explanation','trace','pause','resume','cancel','delete-preview','delete-apply') and not a.id:p.error('Provide --id.')
    if a.output and a.output.resolve().is_relative_to(a.state_dir.resolve()):p.error('Export outside the catalogue.')
    try:
        if a.server:
            client=LocalClient(a.server);base='/api/comparisons'
            if a.command=='run':
                def side(value):
                    kind,key=value.split(':',1);return {'kind':kind,'id':key}
                if not a.left or not a.right:raise ValueError('Provide --left and --right as job:ID or replay:ID.')
                key=a.id or uuid.uuid4().hex
                value=client.call(base,{'request_id':key,'left':side(a.left),'right':side(a.right),'mode':a.mode})
                if a.wait:
                    deadline=time.monotonic()+3600
                    while value['state']['status'] in ('queued','running') and time.monotonic()<deadline:
                        time.sleep(.2);value=client.call(base+'/'+key)
            elif group=='compare':
                if a.command=='list':value=client.call(base+f'?start={a.start}&limit={a.limit}')
                elif a.command=='status':value=client.call(base+'/'+a.id)
                elif a.command=='explanation':value=client.call(base+f'/{a.id}/explanation?side={a.side}&start={a.start}&limit={a.limit}')
                else:value=client.call(base+f'/{a.id}/{a.command}',{})
            elif a.command=='list':value=client.call(('/api/traces' if a.kind=='trace' else base)+f'?start={a.start}&limit={a.limit}')
            elif a.command in ('policy','set-policy'):value=client.call('/api/diagnostics/retention',{'keep_days':a.keep_days} if a.command=='set-policy' else None)
            elif a.command=='candidates':value=client.call(f'/api/diagnostics/{a.kind}/candidates?start={a.start}&limit={a.limit}')
            else:value=client.call(f'/api/diagnostics/{a.kind}/{a.id}/{a.command}',{'preview_id':a.preview_id} if a.command=='delete-apply' else {})
        else:
            if not (a.state_dir/'catalog.sqlite3').is_file():raise ValueError('Provide an existing catalogue.')
            from apps.openpine.bootstrap import local_manager
            from apps.openpine.comparisons import Comparisons
            from apps.openpine.traces import Traces
            from apps.openpine.diagnostic_retention import DiagnosticRetention
            with local_manager(a.state_dir,autostart=False) as manager:
                service=Comparisons(manager,start=False);retention=DiagnosticRetention(manager,Traces(manager,start=False),service)
                if a.command=='status':value=service.status(a.id)
                elif a.command=='explanation':
                    from apps.openpine.comparison_trace import explanation
                    value=explanation(service,a.id,a.side,a.start,a.limit)
                elif a.command=='policy':value=retention.policy()
                elif a.command=='candidates':value=retention.candidates(a.kind,a.start,a.limit)
                else:value=retention.list_traces(a.start,a.limit) if group!='compare' and a.kind=='trace' else service.list(a.start,a.limit)
        code=2 if value.get('state',{}).get('status') in ('failed','limited','not_comparable','cancelled') else 0
    except (ValueError,OSError,KeyError) as error:value={'status':'rejected','message':str(error)};code=2
    if a.output:atomic_json(a.output,value)
    else:sys.stdout.write(canonical(value).decode('utf-8')+'\n')
    return code
