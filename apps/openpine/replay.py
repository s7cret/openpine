"""Composition and CLI for finite, isolated offline event replay."""

import argparse
import os
import subprocess
import sys
import tempfile
import time
import itertools
import apsw
from dataclasses import asdict
from pathlib import Path

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.trading import BrokerSettings
from packages.contracts.realtime import policy
from packages.contracts.realtime import PROFILE
from packages.contracts.live import event_policy
from packages.contracts.wire import atomic_json, canonical, loads, read_json
from packages.execution.artifacts import import_program, export_program
from packages.execution.realtime import ReplayEngine
from packages.execution.resources import resident_bytes
from packages.execution.session import Session
from packages.storage.lease import Lease
from packages.storage.replay import ReplayRepository
from packages.storage.results import series_rows
from packages.storage.trades import event_rows

ROOT = Path(__file__).resolve().parents[2]


class EmptySink:
    def enable_simulation(self): pass


class Replays:
    def __init__(self, manager):
        self.manager = manager
        self.repository = ReplayRepository(manager.db, manager.objects)

    def create(self, payload):
        names = {'input_ref','contexts','primary_id','broker_settings','timeout_seconds'}
        if type(payload) is not dict or payload.keys() not in (names,names|{'event_profile'}): raise ValueError('Invalid replay creation fields.')
        if (type(payload['contexts']) is not list or not 1 <= len(payload['contexts']) <= 8
                or type(payload['timeout_seconds']) not in (int,float) or not 0 < payload['timeout_seconds'] <= 3600):
            raise ValueError('Invalid replay contexts/timeout.')
        with self.manager.limiter.slot('prepare'):
            content = self.manager.objects.get(payload['input_ref'])
            session = Session(profile_id=HISTORICAL_PROFILE_ID)
            if 'artifact_ref' in content:
                program = import_program(self.manager.objects.get(content['artifact_ref']))
            elif content.keys() == {'source','source_name'}:
                program = session.compile(content['source'],content['source_name'])
            else: raise ValueError('Replay input needs source or compiled IR.')
            contexts = tuple(MarketContext.from_payload(c) for c in payload['contexts'])
            settings = BrokerSettings.from_payload(payload['broker_settings']) if payload['broker_settings'] is not None else None
            profile=payload.get('event_profile',PROFILE)
            engine = ReplayEngine(session, program, contexts, payload['primary_id'], EmptySink(), broker_settings=settings,profile=profile)
            config = {'schema_version':'openpine.replay-config.1', **payload,
                'artifact_ref':self.manager.objects.put(export_program(program)), 'identity':engine.identity,
                'event_profile':profile,'policy':event_policy(program.identity.language_version,profile),
                'memory_mb':self.manager.policy.memory_mb_per_worker, 'threads':self.manager.policy.threads_per_worker,
                'heartbeat_timeout':self.manager.policy.heartbeat_timeout,
                'object_quota_bytes':self.manager.objects.quota_bytes}
            identifier = self.repository.create(config)
            return self.repository.status(identifier)

    def append(self, identifier, events):
        with self.manager.limiter.slot('prepare'):
            self.repository.append(identifier, events)
            with tempfile.TemporaryDirectory(prefix='replay-tape-',dir=self.manager.directory) as temporary:
                self.repository.archive(identifier,temporary)
            return self.status(identifier)
    def status(self, identifier): return self.repository.status(identifier)

    def advance(self, identifier, *, max_events=10000000, checkpoint_every=10000, cancelled=lambda:False):
        if (type(max_events) is not int or not 1 <= max_events <= 10000000
                or type(checkpoint_every) is not int or not 1 <= checkpoint_every <= 1000000):
            raise ValueError('Invalid replay execution/checkpoint limit.')
        config = self.repository.config(identifier);started=time.monotonic();peak=0;native_metrics={}
        with self.manager.reserve_worker(config['memory_mb'], config['threads']):
            # A per-replay lease prevents two attempts from publishing the same
            # output prefix. The coordinator lease keeps GC away from attempts.
            with Lease(self.manager.directory/('.replay-'+identifier+'.lock')):
                with tempfile.TemporaryDirectory(prefix='replay-attempt-', dir=self.manager.directory) as temporary:
                    path = Path(temporary)
                    args = [sys.executable,'-X','utf8','-c',
                        "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.replay_worker',run_name='__main__')",
                        str(ROOT),'--store',str(self.manager.directory),'--id',identifier,'--attempt',str(path),
                        '--max-events',str(max_events),'--checkpoint-every',str(checkpoint_every),'--parent-pid',str(os.getpid())]
                    with (path/'worker.log').open('wb') as log:
                        process = subprocess.Popen(args, cwd=path, stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                            env={**os.environ,'OMP_NUM_THREADS':str(config['threads']),'OPENBLAS_NUM_THREADS':str(config['threads'])},
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        deadline = time.monotonic()+config['timeout_seconds']
                        born = time.monotonic()
                        try:
                            while process.poll() is None:
                                if cancelled():raise InterruptedError('Replay attempt stopped; the published prefix remains resumable.')
                                if time.monotonic() > deadline: raise ValueError('Replay worker exceeded its timeout; the published prefix remains resumable.')
                                rss=resident_bytes(process.pid);peak=max(peak,rss)
                                if rss > config['memory_mb']*1024**2: raise ValueError('Replay worker exceeded its resident memory budget.')
                                pulse=path/'heartbeat.json'
                                if pulse.exists():
                                    tick=read_json(pulse,max_bytes=4096)['tick']
                                    if time.monotonic()-tick>config['heartbeat_timeout']: raise ValueError('Replay worker heartbeat expired.')
                                elif time.monotonic()-born>config['heartbeat_timeout']: raise ValueError('Replay worker did not start its heartbeat.')
                                time.sleep(.05)
                            if process.returncode:
                                error = read_json(path/'error.json') if (path/'error.json').exists() else {'message':f'Replay worker exited with {process.returncode}.'}
                                raise ValueError(error['message'])
                            if (path/'metrics.json').exists():native_metrics=read_json(path/'metrics.json',max_bytes=4096)
                        finally:
                            if process.poll() is None: process.kill()
                            process.wait(timeout=10)
        self.last_metrics={'wall_seconds':time.monotonic()-started,'peak_rss_sampled_bytes':peak,'worker_launches':1,**native_metrics}
        return self.status(identifier)

    def advance_many(self,identifiers,*,max_events=1000,checkpoint_every=1000,cancelled=lambda identifier:False):
        from apps.openpine.replay_batch import advance_many
        return advance_many(self,identifiers,max_events=max_events,checkpoint_every=checkpoint_every,cancelled=cancelled)

    def view(self, identifier, action, start=0, limit=2000):
        if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 5000: raise ValueError('Invalid replay range.')
        state = self.status(identifier)
        if action == 'chart':
            end = min(state['confirmed_bars'],start+max(1,min(limit,5000//max(1,len(state['outputs'])))))
            rows = list(itertools.islice(series_rows(self.manager.objects, {'observation_count':state['confirmed_bars'],'series':state['series']},start,end),5000))
            return {'rows':rows,'preview':state['preview'],'total':state['confirmed_bars'],'next':end}
        if action != 'ledger': raise ValueError('Unknown replay view.')
        journal = (state['simulation'] or {}).get('journal',{'records':0,'parts':[]})
        events = list(event_rows(self.manager.objects,journal,start,limit))
        return {'events':events,'total':journal['records'],'next':start+len(events)}


def main(argv=None):
    from apps.openpine.bootstrap import local_manager
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('create','append','advance','status','chart','ledger'))
    parser.add_argument('--state-dir',type=Path,default=Path('.runtime/jobs'))
    parser.add_argument('--id')
    parser.add_argument('--source',type=Path)
    parser.add_argument('--artifact',type=Path)
    parser.add_argument('--contexts',type=Path,help='JSON with contexts, primary_id and optional broker_settings')
    parser.add_argument('--events',type=Path,help='streamed market-event NDJSON')
    parser.add_argument('--timeout',type=float,default=60)
    parser.add_argument('--max-events',type=int,default=10000000)
    parser.add_argument('--checkpoint-every',type=int,default=10000)
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--limit',type=int,default=2000)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() in {p.resolve() for p in (args.source,args.artifact,args.contexts,args.events) if p}: parser.error('Output must not overwrite an input.')
    if args.command == 'create':
        if bool(args.source) == bool(args.artifact) or not args.contexts or args.id: parser.error('Create requires one --source/--artifact and --contexts.')
    elif not args.id or args.source or args.artifact or args.contexts: parser.error('Existing replay commands require --id and use their pinned configuration.')
    if (args.command == 'append') != bool(args.events): parser.error('Append requires --events; other commands do not accept it.')
    try:
        with local_manager(args.state_dir) as manager:
            service = Replays(manager)
            if args.command == 'create':
                if args.source:
                    with args.source.open('rb') as stream: text=stream.read(65537).decode('utf-8')
                    content={'source':text,'source_name':args.source.name}
                else:
                    from packages.execution.artifacts import read_program
                    content={'artifact':export_program(read_program(args.artifact))}
                inputs=read_json(args.contexts)
                if type(inputs) is not dict or inputs.keys() not in ({'contexts','primary_id'},{'contexts','primary_id','broker_settings'}): raise ValueError('Invalid replay context file.')
                value=service.create({'input_ref':manager.freeze_input(content),**inputs,
                    'broker_settings':inputs.get('broker_settings'),'timeout_seconds':args.timeout})
            elif args.command == 'append':
                batch=[]
                with args.events.open('rb') as stream:
                    while raw := stream.readline(65537):
                        batch.append(loads(raw,max_bytes=65536))
                        if len(batch)==1000: service.append(args.id,batch);batch=[]
                if batch: service.append(args.id,batch)
                value=service.status(args.id)
            elif args.command == 'advance': value=service.advance(args.id,max_events=args.max_events,checkpoint_every=args.checkpoint_every)
            elif args.command == 'status': value=service.status(args.id)
            else: value=service.view(args.id,args.command,args.start,args.limit)
        code=0
    except (ValueError,OSError,apsw.Error) as error:
        value={'status':'rejected','diagnostics':[{'code':'REPLAY_INVALID','message':str(error)}]};code=2
    if args.output: atomic_json(args.output,value)
    else: sys.stdout.buffer.write(canonical(value)+b'\n')
    return code
