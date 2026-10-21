"""Observed-only causality, rollback, durable prefixes and transport acceptance."""

import copy
import errno
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

from apps.openpine.bootstrap import local_manager, local_services
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, HistoricalData
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent, policy
from packages.contracts.wire import canonical, loads
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.backup import backup, restore
from packages.storage.replay import ReplayRepository


PRIMARY=MarketContext('primary','TEST:REPLAY','1','recorded.1')
HIGHER=MarketContext('higher','TEST:REPLAY','2','recorded.1')


class Sink:
    def __init__(self): self.rows=[];self.events=[]
    def emit(self,site,title,value_type,value,index,timestamp): self.rows.append((index,timestamp,site,value))
    def enable_simulation(self): pass
    def simulation_emit(self,events): self.events.extend(copy.deepcopy(events))


def text(version=6,strategy=False):
    ema='ta.ema' if version>=5 else 'ema'
    declaration='indicator' if version>=5 else 'study'
    if strategy:
        direction='true' if version==1 else 'strategy.long'
        return f'//@version={version}\nstrategy("replay",default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1)\nif bar_index==0\n    strategy.entry("L",{direction})\nif bar_index==2\n    strategy.close("L")\nplot(strategy.equity)\nplot(strategy.position_size)'
    variable='var n=0\nn:=n+1\nplot(n)\n' if version>=4 else ''
    return f'//@version={version}\n{declaration}("replay")\n{variable}plot({ema}(close,2))\nplot(close[1])'


def tape(count=4,context=PRIMARY,offset=0):
    duration=int(context.timeframe)*60000
    for index in range(count):
        t=index*duration;p=float(10+index*2+offset)
        for revision,kind,at,close,volume in ((0,'open',t,p,0.),(1,'update',t+duration//2,p+0.5,1.),(2,'close',t+duration,p+1.,2.)):
            yield MarketEvent(index*3+revision,context.context_id,kind,t,at,revision,p,max(p,close),p,close,volume)


def historical(events,context=PRIMARY):
    return HistoricalData(context.context_id,context.symbol,context.timeframe,tuple(HistoricalBar(e.time,e.close,e.open,e.high,e.low,e.volume) for e in events if e.kind=='close' and e.context_id==context.context_id),schema_version='openpine.historical-bars.2')


def configuration(manager,source):
    return {'input_ref':manager.freeze_input({'source':source,'source_name':'replay.pine'}),
        'contexts':[asdict(PRIMARY)],'primary_id':PRIMARY.context_id,'broker_settings':None,'timeout_seconds':120}


class RealtimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def engine(self,source,contexts=(PRIMARY,)):
        sink=Sink();program=self.session.compile(source,'replay.pine')
        return ReplayEngine(self.session,program,contexts,PRIMARY.context_id,sink),sink

    def test_all_versions_rollback_close_equivalence_and_persistent_variables(self):
        for version in range(1,7):
            events=list(tape());engine,sink=self.engine(text(version))
            for event in events:
                before=len(sink.rows);engine.apply(event)
                if event.kind!='close':
                    self.assertEqual(len(sink.rows),before)
                    self.assertEqual(engine.evaluation.count,event.sequence//3)
                    self.assertIsNotNone(engine.preview)
                    if version>=4:self.assertEqual(engine.preview['outputs'][0]['value'],event.sequence//3+1)
            _,expected=self.session.run(text(version),'replay.pine',data=historical(events))
            for output in expected.outputs:
                self.assertEqual(tuple(r[3] for r in sink.rows if r[2]==output.output_id),output.values)
            self.assertEqual(engine.evaluation.count,4)

    def test_all_versions_strategy_orders_once_next_open_and_joint_resume_every_event(self):
        for version in range(1,7):
            events=list(tape());engine,sink=self.engine(text(version,True))
            for event in events:engine.apply(event)
            expected=copy.deepcopy(sink.events)
            _,direct=self.session.run(text(version,True),'replay.pine',data=historical(events))
            self.assertEqual(expected,list(direct.simulation['events']))
            self.assertEqual(engine.simulation.summary()['statistics'],direct.simulation['statistics'])
            for cut in range(1,len(events)+1):
                first,_=self.engine(text(version,True))
                for event in events[:cut]:first.apply(event)
                state=loads(canonical(first.checkpoint()))
                resumed,out=self.engine(text(version,True));resumed.restore(state)
                for event in events[cut:]:resumed.apply(event)
                self.assertEqual(resumed.status(),engine.status())
                prefix=[e for e in expected if e['bar_index']<cut//3]
                self.assertEqual(prefix+out.events,expected)

    def test_indicator_resume_every_event_and_preview_does_not_mutate_state(self):
        events=list(tape());engine,sink=self.engine(text())
        for event in events:engine.apply(event)
        for cut in range(1,len(events)+1):
            first,out=self.engine(text())
            for event in events[:cut]:first.apply(event)
            prefix=list(out.rows)
            resumed,last=self.engine(text());resumed.restore(loads(canonical(first.checkpoint())))
            for event in events[cut:]:resumed.apply(event)
            self.assertEqual(prefix+last.rows,sink.rows)
            self.assertEqual(resumed.status(),engine.status())

    def test_rollback_overlay_for_sma_change_cross_and_seeded_ema(self):
        for version in range(1,7):
            declaration='indicator' if version>=5 else 'study';prefix='ta.' if version>=5 else ''
            source=f'//@version={version}\n{declaration}("kernels")\nplot({prefix}sma(close,3))\nplot({prefix}change(close))\nplot({prefix}ema(close,3))\nplot({prefix}cross(close,11) ? 1 : 0)'
            engine,sink=self.engine(source);events=list(tape(8))
            for event in events:
                before=canonical(engine.evaluation.checkpoint()) if engine.evaluation.count else None
                engine.apply(event)
                if event.kind!='close' and before:self.assertEqual(canonical(engine.evaluation.checkpoint()),before)
            _,expected=self.session.run(source,'replay.pine',data=historical(events))
            for output in expected.outputs:self.assertEqual(tuple(r[3] for r in sink.rows if r[2]==output.output_id),output.values)

    def test_explicit_lookahead_off_close_equivalence_with_higher_context(self):
        from packages.execution.driver import MemorySource
        from packages.contracts.contexts import ContextBinding
        for version in range(3,7):
            declaration='indicator' if version>=5 else 'study';api='request.security' if version>=5 else 'security'
            source=f'//@version={version}\n{declaration}("higher")\nplot({api}("TEST:REPLAY","2",close,lookahead=barmerge.lookahead_off))'
            primary=list(tape(4));secondary=list(tape(2,HIGHER,100))
            # Historical-close-only observed tape has the same availability as
            # the existing lookahead_off historical scheduler.
            events=sorted([e for e in primary+secondary if e.kind=='close'],key=lambda e:(e.event_time,e.context_id=='primary'))
            events=[replace(e,sequence=i,revision=0) for i,e in enumerate(events)]
            engine,sink=self.engine(source,(PRIMARY,HIGHER))
            for event in events:engine.apply(event)
            bars=historical(primary);higher=historical(secondary,HIGHER)
            _,expected=self.session.run(source,'replay.pine',data=bars,primary_context=PRIMARY,
                context_sources=((ContextBinding(HIGHER,'a'*64),MemorySource(higher)),))
            self.assertEqual(tuple(r[3] for r in sink.rows),expected.outputs[0].values)

    def test_checkpoint_profile_and_future_context_tampering(self):
        engine,_=self.engine(text());engine.apply(next(tape()))
        state=loads(canonical(engine.checkpoint()))
        state['members']['primary']['active']['event_time']=999999
        resumed,_=self.engine(text())
        with self.assertRaises(ValueError):resumed.restore(state)

    def test_missing_close_cannot_feed_future_context_into_stale_primary(self):
        engine,_=self.engine(text(),(PRIMARY,HIGHER))
        engine.apply(next(tape()))
        future=MarketEvent(1,'higher','close',0,120000,0,100.,101.,100.,101.,1.)
        with self.assertRaisesRegex(ValueError,'Missing candle close'):engine.apply(future)
        self.assertEqual(engine.sequence,0)

    def test_var_rollback_in_all_existing_version_profiles(self):
        for version in range(1,7):
            declaration='indicator' if version>=5 else 'study'
            engine,sink=self.engine(f'//@version={version}\n{declaration}("vars")\nvar n=0\nn:=n+1\nplot(n)')
            for event in tape():engine.apply(event)
            self.assertEqual([r[3] for r in sink.rows],[1,2,3,4])

    def test_strategy_and_requested_ema_resume_jointly_at_every_event_all_versions(self):
        merged=sorted([*tape(4),*tape(2,HIGHER,100)],key=lambda e:(e.event_time,
            2 if e.kind=='open' else 1 if e.context_id=='primary' else 0))
        events=[replace(event,sequence=i) for i,event in enumerate(merged)]
        for version in range(1,7):
            api='request.security' if version>=5 else 'security';ema='ta.ema' if version>=5 else 'ema'
            source=text(version,True)+f'\nplot({api}("TEST:REPLAY","2",{ema}(close,2)))'
            baseline,expected=self.engine(source,(PRIMARY,HIGHER))
            for event in events:baseline.apply(event)
            for cut in range(1,len(events)+1):
                first,prefix=self.engine(source,(PRIMARY,HIGHER))
                for event in events[:cut]:first.apply(event)
                resumed,tail=self.engine(source,(PRIMARY,HIGHER))
                resumed.restore(loads(canonical(first.checkpoint())))
                for event in events[cut:]:resumed.apply(event)
                self.assertEqual(prefix.rows+tail.rows,expected.rows)
                self.assertEqual(prefix.events+tail.events,expected.events)
                self.assertEqual(resumed.status(),baseline.status())

    def test_contract_duplicates_sequence_revision_corrections_and_gaps(self):
        engine,sink=self.engine(text());events=list(tape())
        engine.apply(events[0]);engine.apply(events[0])
        self.assertEqual(engine.sequence,0)
        for bad in (replace(events[0],close=11.,high=11.),replace(events[1],sequence=2),
                    replace(events[1],revision=3),replace(events[1],open=10.25)):
            with self.assertRaises(ValueError):engine.apply(bad)
        engine.apply(events[1]);engine.apply(events[2])
        with self.assertRaises(ValueError):engine.apply(replace(events[2],sequence=3,revision=0))
        gap=replace(events[6],sequence=3);engine.apply(gap)
        self.assertEqual(engine.evaluation.count,1)
        self.assertEqual(engine.members['primary']['active']['time'],120000)
        for value in (True,1,1.0):
            payload=events[0].payload();payload['sequence']=value
            if type(value) is not int:
                with self.assertRaises(ValueError):MarketEvent.from_payload(payload)

    def test_secondary_updates_recalculate_observed_primary_without_future(self):
        for version in range(1,7):
            api='request.security' if version>=5 else 'security'
            declaration='indicator' if version>=5 else 'study'
            source=f'//@version={version}\n{declaration}("requests")\nplot({api}("TEST:REPLAY","2",close))'
            engine,sink=self.engine(source,(PRIMARY,HIGHER))
            values=[MarketEvent(0,'higher','open',0,0,0,100.,100.,100.,100.,0.),
                MarketEvent(1,'primary','open',0,0,0,10.,10.,10.,10.,0.),
                MarketEvent(2,'higher','update',0,30000,1,100.,105.,100.,105.,1.),
                MarketEvent(3,'primary','close',0,60000,1,10.,11.,10.,11.,1.),
                MarketEvent(4,'primary','open',60000,60000,0,11.,11.,11.,11.,0.),
                MarketEvent(5,'higher','close',0,120000,2,100.,110.,100.,110.,2.),
                MarketEvent(6,'primary','close',60000,120000,1,11.,12.,11.,12.,1.)]
            for event in values[:3]:engine.apply(event)
            self.assertEqual(engine.preview['outputs'][0]['value'],105.)
            checkpoint=loads(canonical(engine.checkpoint()))
            resumed,out=self.engine(source,(PRIMARY,HIGHER));resumed.restore(checkpoint)
            for event in values[3:]:engine.apply(event);resumed.apply(event)
            self.assertEqual([row[3] for row in sink.rows],[105.,110.])
            self.assertEqual(out.rows,sink.rows)
            self.assertEqual(resumed.status(),engine.status())
            # A confirmed higher bar cannot arrive after a primary close at the
            # same availability timestamp and alter an already committed value.
            with self.assertRaises(ValueError):engine.apply(replace(values[-2],sequence=7))

    def test_tampered_joint_checkpoint_is_rejected(self):
        engine,_=self.engine(text());engine.apply(next(tape()))
        state=loads(canonical(engine.checkpoint()))
        for mutate in (lambda s:s.update(identity='0'*64),lambda s:s['members']['primary'].update(count=1),
                       lambda s:s.update(sequence=4),lambda s:s['members']['primary']['active'].update(kind='close')):
            bad=copy.deepcopy(state);mutate(bad)
            resumed,_=self.engine(text())
            with self.assertRaises(ValueError):resumed.restore(bad)


class DurableReplayTests(unittest.TestCase):
    def test_gc_removes_crashed_replay_work_only_after_lease_release(self):
        from packages.storage.lease import Lease
        from packages.contracts.wire import atomic_json
        with tempfile.TemporaryDirectory() as temporary,local_services(Path(temporary)/'store') as services:
            path=services.objects.root.parent/'replay-attempt-abcd1234';path.mkdir()
            atomic_json(path/'owner.json',{'schema_version':'openpine.replay-attempt.1','replay_id':'a'*32})
            (path/'incomplete.csv').write_bytes(b'unpublished output')
            with Lease(path/'.active.lock'):
                services.lifecycle.collect(apply=True,grace_seconds=0)
                self.assertTrue(path.is_dir())
            services.lifecycle.collect(apply=True,grace_seconds=0)
            self.assertFalse(path.exists())

    def test_backup_directory_publication_retries_transient_windows_handle(self):
        from packages.storage.backup import publish_directory
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir();target=root/'target'
            (source/'proof').write_bytes(b'complete backup')
            original=Path.rename;attempts=0
            def rename(path,destination):
                nonlocal attempts
                attempts+=1
                if attempts==1:
                    error=PermissionError('temporary scanner handle');error.winerror=5;raise error
                return original(path,destination)
            with patch('packages.storage.backup.os',SimpleNamespace(name='nt')),patch.object(Path,'rename',rename):publish_directory(source,target)
            self.assertEqual((target/'proof').read_bytes(),b'complete backup')
            source.mkdir()
            with self.assertRaises(FileExistsError):publish_directory(source,target)
            self.assertTrue(source.is_dir())

    def test_native_resume_backup_gc_and_bounded_views(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);events=[e.payload() for e in tape()]
            with local_manager(root/'store') as manager:
                replays=Replays(manager);identifier=replays.create(configuration(manager,text(6,True)))['replay_id']
                replays.append(identifier,events);replays.append(identifier,events[:3])
                state=replays.advance(identifier,max_events=4,checkpoint_every=2)
                self.assertEqual((state['sequence'],state['confirmed_bars']),(3,1))
                self.assertEqual(state['simulation']['statistics']['fills'],1)
                self.assertEqual(replays.view(identifier,'ledger')['total'],2)
                manager.services.lifecycle.collect(apply=True,grace_seconds=0)
                backup(manager.db,manager.directory,root/'backup')
                baseline=replays.advance(identifier)
                chart=replays.view(identifier,'chart');ledger=replays.view(identifier,'ledger')
                self.assertEqual(ledger['total'],9)
                self.assertEqual(len(replays.view(identifier,'ledger',1,2)['events']),2)
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                replays=Replays(manager);resumed=replays.advance(identifier)
                self.assertEqual(resumed['simulation'],baseline['simulation'])
                self.assertEqual(resumed['input_chain'],baseline['input_chain'])
                self.assertEqual(replays.view(identifier,'chart')['rows'],chart['rows'])
                self.assertEqual(replays.view(identifier,'ledger'),ledger)

    def test_transactional_append_conflict_and_missing_sequence(self):
        with tempfile.TemporaryDirectory() as temporary,local_manager(Path(temporary)/'store') as manager:
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            events=[e.payload() for e in tape()]
            with self.assertRaises(ValueError):service.append(identifier,[events[0],events[2]])
            self.assertEqual(service.status(identifier)['accepted_events'],0)
            service.append(identifier,events[:2])
            bad=copy.deepcopy(events[0]);bad['volume']=1.
            with self.assertRaises(ValueError):service.append(identifier,[bad])
            self.assertEqual(service.status(identifier)['accepted_events'],2)

    def test_replay_shares_worker_budget_with_historical_jobs(self):
        with tempfile.TemporaryDirectory() as temporary,local_manager(Path(temporary)/'store',max_workers=1) as manager:
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            service.append(identifier,[e.payload() for e in tape()])
            with manager.reserve_worker(512,1):
                with self.assertRaises(ValueError):service.advance(identifier)
            self.assertEqual(service.advance(identifier)['confirmed_bars'],4)

    def test_real_worker_kill_preserves_published_midbar_prefix(self):
        with tempfile.TemporaryDirectory() as temporary,local_manager(Path(temporary)/'store') as manager:
            root=Path(temporary);service=Replays(manager)
            identifier=service.create(configuration(manager,text(6,True)))['replay_id']
            events=[]
            for event in tape(12000):
                events.append(event.payload())
                if len(events)==1000:service.append(identifier,events);events=[]
            if events:service.append(identifier,events)
            attempt=root/'kill';attempt.mkdir()
            process=subprocess.Popen([sys.executable,'-X','utf8','-m','apps.replay_worker','--store',str(manager.directory),
                '--id',identifier,'--attempt',str(attempt),'--max-events','100000','--checkpoint-every','1000','--parent-pid',str(__import__('os').getpid())],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    state=service.status(identifier)
                    if state['sequence']>=999:break
                    if process.poll() is not None:self.fail('Worker exited before the crash boundary.')
                    time.sleep(.02)
                self.assertGreaterEqual(state['sequence'],999)
                process.kill();process.wait(timeout=10)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=10)
            final=service.advance(identifier)
            self.assertEqual((final['sequence'],final['confirmed_bars']),(35999,12000))
            self.assertEqual(final['simulation']['statistics']['orders'],2)
            self.assertEqual(final['simulation']['statistics']['fills'],2)
            ledger=service.view(identifier,'ledger',0,20)['events']
            self.assertEqual(len([e for e in ledger if e['kind']=='fill']),2)

    def test_parquet_tape_archive_and_old_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as temporary,local_manager(Path(temporary)/'store') as manager:
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            events=[e.payload() for e in tape()];service.append(identifier,events)
            with patch('packages.storage.replay.BLOCK',3):service.repository.archive(identifier,Path(temporary)/'pack')
            self.assertEqual([e.payload() for e in service.repository.events(identifier,2,8)],events[2:8])
            service.append(identifier,events[:4])
            state=service.advance(identifier)
            self.assertEqual(state['confirmed_bars'],4)

    def test_compaction_during_read_does_not_skip_an_accepted_tail(self):
        with tempfile.TemporaryDirectory() as temporary,local_manager(Path(temporary)/'store') as manager:
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            events=[e.payload() for e in tape()];service.append(identifier,events)
            execute=manager.db.execute;compacted=False
            def race(sql,bindings=()):
                nonlocal compacted
                result=execute(sql,bindings)
                if sql.startswith('SELECT first_sequence,rows,object_id FROM replay_parts') and not compacted:
                    compacted=True
                    service.repository.archive(identifier,Path(temporary)/'pack')
                return result
            with patch('packages.storage.replay.BLOCK',3),patch.object(manager.db,'execute',side_effect=race):
                actual=[e.payload() for e in service.repository.events(identifier,0,8)]
            self.assertEqual(actual,events[:8])

    def test_failed_publication_keeps_previous_prefix_and_disk_full_is_recoverable(self):
        from apps.replay_worker import execute
        with tempfile.TemporaryDirectory() as temporary,local_manager(Path(temporary)/'store') as manager:
            root=Path(temporary);service=Replays(manager)
            identifier=service.create(configuration(manager,text(6,True)))['replay_id']
            service.append(identifier,[e.payload() for e in tape()]);service.advance(identifier,max_events=4)
            previous=service.status(identifier)['checkpoint_ref']
            with patch('packages.storage.replay.ReplayRepository.publish',side_effect=OSError(errno.ENOSPC,'injected disk full')):
                with self.assertRaises(OSError):execute(manager.directory,identifier,root/'failed')
            self.assertEqual(service.status(identifier)['checkpoint_ref'],previous)
            final=service.advance(identifier)
            self.assertEqual(final['simulation']['statistics']['fills'],2)

    def test_cli_separate_process_commands_and_http_worker_equality(self):
        from apps.ui.server import Server
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'source.pine').write_bytes(text().encode())
            (root/'contexts.json').write_bytes(canonical({'contexts':[asdict(PRIMARY)],'primary_id':'primary'}))
            (root/'tape.ndjson').write_bytes(b''.join(canonical(e.payload())+b'\n' for e in tape()))
            def cli(*args):
                result=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','replay',*args,'--state-dir',str(root/'cli')],capture_output=True,timeout=90)
                self.assertEqual(result.returncode,0,result.stdout.decode()+result.stderr.decode())
                return loads(result.stdout)
            identifier=cli('create','--source',str(root/'source.pine'),'--contexts',str(root/'contexts.json'))['replay_id']
            cli('append','--id',identifier,'--events',str(root/'tape.ndjson'))
            cli('advance','--id',identifier,'--max-events','4')
            expected=cli('advance','--id',identifier);chart=cli('chart','--id',identifier)
            server=Server(('127.0.0.1',0),root/'web');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def http(path,payload=None):
                request=Request(f'http://127.0.0.1:{server.server_port}'+path,data=canonical(payload) if payload is not None else None,
                    headers={'Content-Type':'application/json','X-OpenPine-Token':server.token})
                with urlopen(request,timeout=90) as response:return loads(response.read())
            try:
                compiled=http('/api/compile',{'source':text(),'source_name':'source.pine'})
                created=http('/api/replays',{'input_ref':compiled['input_ref'],'contexts':[asdict(PRIMARY)],'primary_id':'primary','broker_settings':None,'timeout_seconds':60})
                identifier=created['replay_id']
                http(f'/api/replays/{identifier}/events',{'events':[e.payload() for e in tape()]})
                actual=http(f'/api/replays/{identifier}/advance',{'max_events':100,'checkpoint_every':10000})
                self.assertEqual(actual['confirmed_bars'],expected['confirmed_bars'])
                self.assertEqual(http(f'/api/replays/{identifier}/chart')['rows'],chart['rows'])
                self.assertEqual(http(f'/api/replays/{identifier}')['sequence'],11)
            finally:server.shutdown();thread.join(timeout=10);server.server_close()


if __name__=='__main__':unittest.main()
