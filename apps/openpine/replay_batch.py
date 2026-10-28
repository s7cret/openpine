"""One finite replay process amortizes catalog loading across admitted sessions."""
import os
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from packages.contracts.realtime import replay_id
from packages.contracts.wire import atomic_json,read_json
from packages.execution.resources import resident_bytes
from packages.storage.lease import Lease

ROOT=Path(__file__).resolve().parents[2]


def advance_many(service,identifiers,*,max_events=1000,checkpoint_every=1000,cancelled=lambda identifier:False):
    if (type(identifiers) is not list or not 1<=len(identifiers)<=8 or len(set(identifiers))!=len(identifiers)
            or any(type(v) is not int or not 1<=v<=10000 for v in (max_events,checkpoint_every))):raise ValueError('Invalid finite replay batch.')
    for identifier in identifiers:replay_id(identifier)
    interrupted=[{'replay_id':key,'status':'interrupted','finished_tick':time.monotonic()} for key in identifiers if cancelled(key)]
    omitted={row['replay_id'] for row in interrupted};identifiers=[key for key in identifiers if key not in omitted]
    if not identifiers:return {'results':interrupted,'metrics':{'child_cpu_seconds':0.,'child_wall_seconds':0.,
        'catalog_load_seconds':0.,'wall_seconds':0.,'peak_rss_sampled_bytes':0,'worker_launches':0}}
    configs={key:service.repository.config(key) for key in identifiers};manager=service.manager
    memory=max(c['memory_mb'] for c in configs.values());threads=max(c['threads'] for c in configs.values())
    started=time.monotonic();peak=0
    with manager.reserve_worker(memory,threads,request_id="batch:"+":".join(sorted(identifiers))),ExitStack() as leases:
        for identifier in sorted(identifiers):leases.enter_context(Lease(manager.directory/('.replay-'+identifier+'.lock')))
        with tempfile.TemporaryDirectory(prefix='replay-attempt-',dir=manager.directory) as directory:
            attempt=Path(directory);atomic_json(attempt/'request.json',{'ids':identifiers,'max_events':max_events,'checkpoint_every':checkpoint_every})
            command=[sys.executable,'-X','utf8','-c',"import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.replay_batch_worker',run_name='__main__')",
                str(ROOT),'--store',str(manager.directory),'--attempt',str(attempt),'--request',str(attempt/'request.json'),'--parent-pid',str(os.getpid())]
            with (attempt/'worker.log').open('wb') as log:
                from packages.execution.supervisor import Supervisor
                supervisor=manager.start_worker(command,attempt,log,threads=threads,
                    timeout=sum(c['timeout_seconds'] for c in configs.values())+min(c['heartbeat_timeout'] for c in configs.values()),
                    heartbeat_timeout=min(c['heartbeat_timeout'] for c in configs.values()),memory_mb=memory)
                process=supervisor.process
                try:
                    while process.poll() is None:
                        pulse=supervisor.poll();config=None;rss=supervisor.rss;peak=supervisor.peak
                        if pulse is not None:

                            identifier=pulse['replay_id'];config=configs.get(identifier)
                            if time.monotonic()-pulse['tick']>min(c['heartbeat_timeout'] for c in configs.values()):raise ValueError('Replay batch heartbeat expired.')
                            if config and time.monotonic()-pulse['started']>config['timeout_seconds']:raise ValueError('Replay batch member exceeded its timeout.')
                            if config and cancelled(identifier):raise InterruptedError('Replay batch member stopped; all published prefixes remain resumable.')
                        elif time.monotonic()-started>min(c['heartbeat_timeout'] for c in configs.values()):raise ValueError('Replay batch did not start its heartbeat.')
                        if rss>(config['memory_mb'] if config else memory)*1024**2:raise ValueError('Replay batch exceeded its resident memory budget.')
                        if time.monotonic()-started>sum(c['timeout_seconds'] for c in configs.values())+min(c['heartbeat_timeout'] for c in configs.values()):raise ValueError('Replay batch exceeded its aggregate time budget.')
                        time.sleep(.05)
                    if process.returncode:
                        message=read_json(attempt/'error.json')['message'] if (attempt/'error.json').exists() else f'Replay batch exited with {process.returncode}.'
                        raise ValueError(message)
                    result=read_json(attempt/'result.json')
                    if {r['replay_id'] for r in result['results']}!=set(identifiers):raise ValueError('Replay batch omitted an admitted member.')
                finally:
                    manager.stop_worker(supervisor)
    waiting=next((r.get('waiting') for r in result['results'] if r['status']=='waiting_resources'),None)
    if waiting:manager.note_worker_waiting('batch:'+':'.join(sorted(identifiers)),waiting)
    result['results'].extend(interrupted)
    result['metrics'].update(wall_seconds=time.monotonic()-started,peak_rss_sampled_bytes=peak,worker_launches=1)
    service.last_metrics=result['metrics'];return result
