"""Keyed diagnostic submissions to the existing local server; offline reads never run work."""
import argparse,sys,time,uuid
from pathlib import Path
from apps.openpine.http_client import LocalClient
from packages.contracts.wire import canonical,atomic_json

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('run','status','events','pause','resume','cancel'))
    p.add_argument('--server');p.add_argument('--state-dir',type=Path,default=Path('.runtime/jobs'));p.add_argument('--id')
    source=p.add_mutually_exclusive_group();source.add_argument('--launch-id');source.add_argument('--replay-id')
    p.add_argument('--binding-sha256');p.add_argument('--start-bar',type=int,default=0);p.add_argument('--end-bar',type=int,default=1)
    p.add_argument('--line',type=int,action='append',default=[]);p.add_argument('--site',action='append',default=[])
    p.add_argument('--max-events',type=int,default=10000);p.add_argument('--max-bytes',type=int,default=8*1024*1024)
    p.add_argument('--start',type=int,default=0);p.add_argument('--limit',type=int,default=200);p.add_argument('--wait',action='store_true')
    p.add_argument('--output',type=Path);a=p.parse_args(argv)
    if a.command not in ('status','events') and not a.server:p.error('Use --server for submissions and controls; offline mode only reads.')
    if a.command=='run' and not (a.launch_id or a.replay_id):p.error('Select a saved preparation or replay.')
    if a.command!='run' and not a.id:p.error('Provide --id.')
    if a.output and a.output.resolve().is_relative_to(a.state_dir.resolve()):p.error('Export outside the catalogue.')
    try:
        if a.server:
            client=LocalClient(a.server)
            if a.command=='run':
                kind='launches' if a.launch_id else 'replays';source=a.launch_id or a.replay_id
                report=client.call(f'/api/{kind}/{source}/passport')['passport'];bound=report['binding' if a.launch_id else 'replay_binding']['sha256']
                key=a.id or uuid.uuid4().hex
                value=client.call('/api/traces',{'request_id':key,'launch_id' if a.launch_id else 'replay_id':source,'binding_sha256':a.binding_sha256 or bound,
                    'options':{'start_bar':a.start_bar,'end_bar':a.end_bar,'lines':a.line,'sites':a.site,'max_events':a.max_events,'max_bytes':a.max_bytes}})
                if a.wait:
                    deadline=time.monotonic()+3600
                    while value['job']['status'] in ('queued','running') and time.monotonic()<deadline:
                        time.sleep(.2);value=client.call('/api/traces/'+key)
            elif a.command in ('pause','resume','cancel'):value=client.call(f'/api/traces/{a.id}/{a.command}',{})
            else:value=client.call(f'/api/traces/{a.id}'+(f'/events?start={a.start}&limit={a.limit}' if a.command=='events' else ''))
        else:
            if not (a.state_dir/'catalog.sqlite3').is_file():raise ValueError('Provide an existing catalogue.')
            from apps.openpine.bootstrap import local_manager
            from apps.openpine.traces import Traces
            with local_manager(a.state_dir,autostart=False) as manager:
                service=Traces(manager,start=False)
                value=service.status(a.id) if a.command=='status' else service.page(a.id,a.start,a.limit)
        code=2 if value.get('job',{}).get('status') in ('failed','timed_out','cancelled') else 0
    except (ValueError,OSError,KeyError) as error:value={'status':'rejected','message':str(error)};code=2
    if a.output:atomic_json(a.output,value)
    else:sys.stdout.write(canonical(value).decode('utf-8')+'\n')
    return code
