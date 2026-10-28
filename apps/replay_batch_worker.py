"""Finite fair replay batch: immutable catalog reuse, fresh runtime for every ID."""
import argparse
import gc
import os
import threading
import time
from pathlib import Path

from apps.worker.__main__ import watch_parent
from apps.replay_worker import execute
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.realtime import replay_id
from packages.contracts.wire import atomic_json,read_json
from packages.execution.session import Session
from packages.storage.database import Database
from packages.storage.lease import Lease


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',type=Path,required=True);parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--parent-pid',type=int,required=True)
    args=parser.parse_args(argv);request=read_json(args.request,max_bytes=16384)
    if (request.keys()!={'ids','max_events','checkpoint_every'} or type(request['ids']) is not list
            or not 1<=len(request['ids'])<=8 or len(set(request['ids']))!=len(request['ids'])
            or any(type(request[k]) is not int or not 1<=request[k]<=10000 for k in ('max_events','checkpoint_every'))):raise ValueError('Invalid finite replay batch.')
    for identifier in request['ids']:replay_id(identifier)
    threading.Thread(target=watch_parent,args=(args.parent_pid,),daemon=True).start()
    stopped=threading.Event();lock=threading.Lock();current={'replay_id':None,'started':time.monotonic()}
    def heartbeat():
        while not stopped.is_set():
            with lock:state=dict(current)
            atomic_json(args.attempt/'heartbeat.json',{'tick':time.monotonic(),'pid':os.getpid(),**state})
            stopped.wait(.2)
    pulse=threading.Thread(target=heartbeat,daemon=True);pulse.start()
    started=time.monotonic();cpu=time.process_time();results=[]
    try:
        with Lease(args.attempt/'.active.lock'):
            atomic_json(args.attempt/'owner.json',{'schema_version':'openpine.replay-attempt.1','replay_id':request['ids'][0]})
            load_started=time.monotonic();session=Session(profile_id=HISTORICAL_PROFILE_ID);setup=time.monotonic()-load_started
            db=Database(args.store/'catalog.sqlite3')
            try:
                for identifier in request['ids']:
                    with lock:current.update(replay_id=identifier,started=time.monotonic())
                    desired=db.execute('SELECT desired FROM live_sessions WHERE replay_id=?',(identifier,))
                    if desired and all(row[0]=='stopped' for row in desired):
                        results.append({'replay_id':identifier,'status':'interrupted','finished_tick':time.monotonic()});continue
                    attempt=args.attempt/identifier;attempt.mkdir()
                    before=db.execute('SELECT applied FROM replays WHERE replay_id=?',(identifier,))[0][0]
                    try:
                        state=execute(args.store,identifier,attempt,max_events=request['max_events'],checkpoint_every=request['checkpoint_every'],session=session)
                        result={'replay_id':identifier,'status':'succeeded','events':state['sequence']-before}
                    except Exception as error:
                        import errno
                        waiting=isinstance(error,OSError) and error.errno==errno.ENOSPC and getattr(error,"retryable",True)
                        result={'replay_id':identifier,'status':'waiting_resources' if waiting else 'failed','error':str(error)[:4096],
                            **({'waiting':getattr(error,'admission',{'reason':'disk','retryable':True,'retry_after_ms':1000})} if waiting else {})}
                    results.append({**result,'finished_tick':time.monotonic(),'duration_seconds':time.monotonic()-current['started']});gc.collect()
            finally:db.close()
            atomic_json(args.attempt/'result.json',{'results':results,'metrics':{'child_cpu_seconds':time.process_time()-cpu,
                'child_wall_seconds':time.monotonic()-started,'catalog_load_seconds':setup}})
    except Exception as error:
        atomic_json(args.attempt/'error.json',{'message':str(error)});return 2
    finally:stopped.set();pulse.join(timeout=2)
    return 0


if __name__=='__main__':raise SystemExit(main())
