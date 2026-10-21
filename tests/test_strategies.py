"""Independent trading arithmetic, phase order, resume and transport acceptance."""

import copy
import errno
import json
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict,replace
from pathlib import Path

from apps.openpine.bootstrap import local_manager,local_services
from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar,OpenPineError
from packages.contracts.trading import BrokerSettings
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical,loads
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.session import Session
from packages.marketdata import data_identity
from packages.marketdata.providers import make_snapshot
from packages.storage.backup import backup,restore
from test_jobs import wait
from test_checkpoints import paused


def data(count=4):
    return HistoricalData('strategy.primary','TEST:STRATEGY','1',tuple(HistoricalBar(i*60000,float(11+2*i),float(10+2*i),
        float(11+2*i),float(10+2*i),1.) for i in range(count)),schema_version='openpine.historical-bars.2')


def source(version=6,body=None,extra=''):
    if body is None:
        direction='true' if version==1 else 'strategy.long'
        body=f'if bar_index==0\n    strategy.entry("L",{direction})\nif bar_index==2\n    strategy.close("L")\nplot(strategy.equity)\nplot(strategy.position_size)\nplot(strategy.position_size[1])'
    return f'//@version={version}\nstrategy("market",default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1{extra})\n{body}'


def request(manager,version=6,*,text=None,bars=None,settings=None):
    snapshot=manager.market.save(make_snapshot(bars or data(),{'provider':'uploaded-file.1','gap_policy':'preserve'}))
    value={'schema_version':'openpine.job-request.2','input_ref':manager.freeze_input({'source':text or source(version),'source_name':f'v{version}.pine'}),
        'snapshot_id':snapshot,'timeout_seconds':120}
    if settings is not None:value.update(schema_version='openpine.job-request.5',primary_context=None,contexts=[],broker_settings=asdict(settings))
    return value


class StrategyRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def driver(self,program,bars=None,settings=None):
        runtime=self.session.validate_program(program)
        return HistoricalDriver(runtime.prepare(program),MemorySource(bars or data()),simulation_policy=broker_policy(program,settings))

    def test_all_versions_next_open_commission_position_history(self):
        for version in range(1,7):
            with self.subTest(version=version):
                _,result=self.session.run(source(version),data=data())
                self.assertEqual(result.schema_version,'openpine.run-result.4')
                self.assertEqual([o.values for o in result.outputs],[(1000.,1000.88,1002.88,1003.72),(0.,1.,1.,0.),(None,0.,1.,1.)])
                simulation=result.simulation;stats=simulation['statistics']
                self.assertEqual(stats,{'cash':'1003.72','equity':'1003.72','position_size':'0','position_avg_price':None,
                    'gross_realized':'4','unrealized':'0','commission':'0.28','orders':2,'fills':2,'trades':1})
                fills=[e['value'] for e in simulation['events'] if e['kind']=='fill']
                self.assertEqual([(f['bar_index'],f['price'],f['commission']) for f in fills],[(1,'12','0.12'),(3,'16','0.16')])
                trade=next(e['value'] for e in simulation['events'] if e['kind']=='trade')
                self.assertEqual((trade['gross_profit'],trade['net_profit']),('4','3.72'))

    def test_independent_crossover_and_higher_context_corpus(self):
        from tools.conformance import run_cases,STRATEGY_CASES
        report=run_cases(self.session,cases_path=STRATEGY_CASES)
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(18,6))

    def test_all_joint_context_and_broker_boundaries_and_requested_ema(self):
        from tools.conformance import context_inputs
        from packages.marketdata import read_bars
        example=ROOT/'examples/strategies';bars=read_bars(example/'wave.json')
        for version in range(1,7):
            text=(example/f'v{version}-filtered.pine').read_text(encoding='utf-8')
            name=f'v{version}-filtered.pine';program=self.session.compile(text,name)
            def driver():
                inputs=context_inputs(example/'inputs.json');source_=MemorySource(bars);runtime=self.session.validate_program(program)
                contexts=self.session.context_scheduler(program,runtime,source_,inputs['primary_context'],inputs['context_sources'])
                return HistoricalDriver(runtime.prepare(program),source_,contexts,broker_policy(program))
            base=driver();base.run();expected=base.finish(data_identity(bars))
            for cut in range(1,9):
                saved=[];first=driver();first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                resumed=driver();resumed.restore(saved[0]);resumed.run()
                self.assertEqual(resumed.finish(data_identity(bars)),expected)
            # Private context programs must use the indicator declaration even
            # when their primary program is a strategy with no explicit plots.
            ema='ema(close,2)' if version<5 else 'ta.ema(close,2)'
            text=text.replace(',"4",close)',f',"4",{ema})')
            text=text.rsplit('plot(',1)[0]
            self.session.run(text,name,data=bars,**context_inputs(example/'inputs.json'))

    def test_short_reversal_no_pyramiding_and_last_pending_order(self):
        text=source(body='''if bar_index==0
    strategy.entry("L",strategy.long,2)
if bar_index==1
    strategy.entry("ignored",strategy.long,2)
if bar_index==2
    strategy.entry("S",strategy.short,3)
if bar_index==3
    strategy.close("S")
if bar_index==4
    strategy.entry("last",strategy.long)
plot(strategy.equity)''')
        _,result=self.session.run(text,data=data(5));stats=result.simulation['statistics']
        self.assertEqual((stats['equity'],stats['commission'],stats['gross_realized'],stats['fills'],stats['trades']),('1000.42','1.58','2',4,2))
        self.assertEqual(result.simulation['pending_orders'][0]['intent']['entry_id'],'last')
        self.assertEqual(len([e for e in result.simulation['events'] if e['kind']=='ignored']),1)

    def test_explicit_tick_slippage_and_half_even_fee_rounding(self):
        settings=BrokerSettings('0.5','0.1',2)
        text=source(extra=',slippage=1')
        with self.assertRaisesRegex(OpenPineError,'tick'):self.session.run(text,data=data())
        _,result=self.session.run(text,data=data(),broker_settings=settings)
        self.assertEqual(result.simulation['statistics']['equity'],'1002.72')
        fills=[e['value'] for e in result.simulation['events'] if e['kind']=='fill']
        self.assertEqual([(f['price'],f['commission']) for f in fills],[('12.5','0.12'),('15.5','0.16')])

    def test_same_id_replaces_pending_command_and_close_wrong_id_is_ignored(self):
        text=source(body='''if bar_index==0
    strategy.entry("L",strategy.long,1)
    strategy.entry("L",strategy.short,2)
if bar_index==1
    strategy.close("other")
plot(strategy.position_size)''')
        _,result=self.session.run(text,data=data())
        self.assertEqual(result.outputs[0].values,(0.,-2.,-2.,-2.))
        self.assertEqual(result.simulation['statistics']['fills'],1)
        self.assertEqual(sum(e['kind']=='replaced' for e in result.simulation['events']),1)

    def test_all_bar_boundaries_all_versions_restore_pending_positions_history(self):
        for version in range(1,7):
            program=self.session.compile(source(version));base=self.driver(program);base.run();expected=base.finish(data_identity(data()))
            for cut in range(1,5):
                with self.subTest(version=version,cut=cut):
                    driver=self.driver(program);saved=[]
                    self.assertFalse(driver.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint())))))
                    resumed=self.driver(program);resumed.restore(saved[-1]);self.assertTrue(resumed.run())
                    self.assertEqual(canonical(asdict(resumed.finish(data_identity(data())))),canonical(asdict(expected)))

    def test_tampered_broker_state_is_rejected(self):
        program=self.session.compile(source());driver=self.driver(program);saved=[]
        driver.run(pause_after=2,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        changes=[lambda s:s['simulation']['broker'].update(cash='0'),lambda s:s['simulation']['broker'].update(cash='invalid'),lambda s:s['simulation']['broker'].update(count=True),
            lambda s:s['simulation']['broker']['position'].update(fill_id='0'*64),lambda s:s['simulation']['broker'].update(fill_count=3),
            lambda s:s.update(context_id='other')]
        for change in changes:
            state=copy.deepcopy(saved[0]);change(state)
            with self.assertRaises(ValueError):self.driver(program).restore(state)

    def test_forged_ir_direction_and_missing_order_parameters_are_rejected(self):
        program=self.session.compile(source(body='strategy.entry("L",strategy.long)'))
        header,order=program.statements
        arguments=list(order.arguments);arguments[1]=replace(arguments[1],expression=replace(arguments[1].expression,value='unknown'))
        for forged in (replace(order,arguments=tuple(arguments)),replace(order,arguments=())):
            with self.assertRaises(OpenPineError):self.session.validate_program(replace(program,statements=(header,forged)))

    def test_reject_unsupported_version_arguments_effect_positions_and_inputs(self):
        bad=[source(body='strategy.entry("L",strategy.long,limit=10)'),
            source(body='strategy.entry("L",strategy.long,when=true)'),
            source(3,body='strategy.entry("L",direction=true)'),
            source(5,body='strategy.entry("L",long=true)'),
            source(body='strategy.entry("L","long")'),
            source(body='id="L"\nstrategy.entry(id,strategy.long)'),
            source(extra=',pyramiding=2'),
            '//@version=6\nstrategy("bad")',
            '//@version=6\nindicator("bad")\nstrategy.entry("L",strategy.long)\nplot(close)',
            source(body='plot(strategy.entry("L",strategy.long))')]
        for text in bad:
            with self.subTest(text=text),self.assertRaises(OpenPineError):self.session.compile(text)
        for body in ['strategy.entry("L",strategy.long,-1)','strategy.entry("L",strategy.long,0.15)']:
            with self.assertRaises((OpenPineError,ValueError)):self.session.run(source(body=body),data=data(),broker_settings=BrokerSettings(quantity_step='0.1'))
        bars=replace(data(),bars=tuple(replace(b,open=None) for b in data().bars))
        with self.assertRaises(ValueError):self.session.run(source(),data=bars)

    def test_legacy_when_and_version_specific_direction_parameter(self):
        for version in range(1,6):
            direction='true' if version<5 else 'strategy.long';parameter='long' if version<5 else 'direction'
            _,result=self.session.run(source(version,body=f'strategy.entry("L",{parameter}={direction},when=false)\nplot(strategy.position_size)'),data=data())
            self.assertEqual(result.simulation['statistics']['orders'],0)


class StrategyWorkerTests(unittest.TestCase):
    def test_wide_journal_seals_at_small_per_connection_budget(self):
        from packages.storage.trades import TradeSink,event_rows
        from packages.contracts.trading import Trade
        value=asdict(Trade('a'*64,'b'*64,'c'*64,'entry'+('Ж'*120),1,'1.25','100.5','110.25','12.1875','0.25','11.9375',0,1))
        with tempfile.TemporaryDirectory() as directory,local_services(Path(directory)/'live') as services:
            sink=TradeSink(services.objects,Path(directory)/'journal',memory_mb=14)
            for index in range(100000):sink.emit([{'sequence':index,'bar_index':index,'time':index*60000,'kind':'trade','value':value}])
            journal=sink.finish();sink.close();self.assertEqual(journal['records'],100000)
            self.assertEqual(list(event_rows(services.objects,journal,99999,1))[0]['value'],value)

    def test_old_run_plan_payloads_preserve_hashes(self):
        from packages.contracts.storage import RunPlan
        from packages.contracts.wire import digest
        for version in (1,2,3):
            plan=RunPlan('a'*64,'b'*64,30,'c'*64,'d'*64,checkpoint_interval_bars=0 if version==1 else 100000,
                schema_version=f'openpine.run-plan.{version}')
            payload=plan.payload();restored=RunPlan.from_payload(payload)
            self.assertEqual(restored.payload(),payload);self.assertEqual(digest(restored.payload()),digest(payload))
            self.assertNotIn('simulation_policy',payload)

    def test_real_worker_and_coordinator_crashes_resume_without_duplicate_fills(self):
        from packages.contracts.storage import ResourcePolicy
        text=source(body='''if bar_index%2==0
    strategy.entry("L",strategy.long)
else
    strategy.close("L")
plot(strategy.equity)''')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live',policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2)) as manager:
                req=request(manager,text=text)
                req['snapshot_id']=manager.market.ingest({'context_id':'strategy.primary','symbol':'TEST:STRATEGY','timeframe':'1',
                    'bar_schema':'openpine.historical-bars.2'},(HistoricalBar(i*60000,float(100+i%13)+.5,float(100+i%13),
                        float(100+i%13)+.5,float(100+i%13),1.) for i in range(100000)),{'provider':'uploaded-file.1'})
                baseline=manager.submit(req,checkpoint_interval_bars=0)['job_id'];state=wait(manager,baseline,timeout=180)
                self.assertEqual(state['status'],'succeeded',state);expected=manager.summary(baseline)['result']
                job=manager.submit(req,checkpoint_interval_bars=10000)['job_id']
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    state=manager.get(job)
                    if state['status']=='running' and state.get('checkpoint_id'):break
                    time.sleep(.01)
                with manager.lock:
                    state=manager.get(job);self.assertEqual(state['status'],'running',state);self.assertTrue(state['checkpoint_id'])
                    manager.children[job][0].kill()
                state=wait(manager,job,timeout=180);self.assertEqual(state['status'],'succeeded',state)
                self.assertGreaterEqual(state['resumed_from_bar'],10000);self.assertEqual(state['attempt_count'],2)
                self.assertEqual(manager.summary(job)['result'],expected)
                (root/'request.json').write_bytes(canonical(req))
            script='''
import sys,time
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import read_json,atomic_json
root=Path(sys.argv[1]);manager=local_manager(root/'live',policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2))
job=manager.submit(read_json(root/'request.json'),checkpoint_interval_bars=10000)['job_id']
while not manager.get(job).get('checkpoint_id'):time.sleep(.01)
atomic_json(root/'ready.json',manager.get(job))
while True:time.sleep(.2)
'''
            host=subprocess.Popen([sys.executable,'-X','utf8','-c',script,str(root)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+60
                while not (root/'ready.json').exists() and host.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((root/'ready.json').exists());old=loads((root/'ready.json').read_bytes())
                host.kill();host.communicate(timeout=5);time.sleep(.3)
                with local_manager(root/'live',max_workers=1) as manager:
                    state=wait(manager,old['job_id'],timeout=180);self.assertEqual(state['status'],'succeeded',state)
                    self.assertGreaterEqual(state['resumed_from_bar'],10000);self.assertEqual(state['attempt_count'],2)
                    self.assertEqual(manager.plan(old['job_id']).max_attempts,2)
                    self.assertEqual(manager.summary(old['job_id'])['result'],expected)
                    stats=expected['simulation']['statistics'];self.assertEqual((stats['orders'],stats['fills'],stats['trades']),(100000,99999,49999))
            finally:
                if host.poll() is None:host.kill();host.communicate(timeout=5)

    def test_failed_journal_append_keeps_previous_checkpoint_usable(self):
        from unittest.mock import patch
        from packages.storage.results import ResultSink
        session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile(source())
        with tempfile.TemporaryDirectory() as directory,local_services(Path(directory)/'live') as services:
            source_=MemorySource(data());source_.bind_identity()
            sink=ResultSink(services.objects,Path(directory)/'first')
            driver=session.stream_driver(program,source_,sink);saved=[]
            driver.run(pause_after=1,checkpoint=lambda d:saved.append((loads(canonical(d.checkpoint())),sink.checkpoint(d.count))))
            with patch.object(sink.trade_sink,'emit',side_effect=OSError(errno.ENOSPC,'Disk full')):
                with self.assertRaises(OSError):driver.run()
            self.assertTrue(driver.evaluation.closed)
            with self.assertRaises(ValueError):driver.checkpoint()
            sink.close()
            sink2=ResultSink(services.objects,Path(directory)/'second');source2=MemorySource(data());source2.bind_identity()
            resumed=session.stream_driver(program,source2,sink2)
            resumed.restore(saved[0][0]);sink2.restore(saved[0][1],resumed.count);sink2.validate_simulation(resumed.simulation.broker)
            resumed.run();result=sink2.finish(resumed.finish_stream(source2.identity));sink2.close()
            self.assertEqual(result['simulation']['statistics']['equity'],'1003.72')

    def test_native_all_versions_pending_checkpoint_new_coordinator_and_exact_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            for version in range(1,7):
                root=Path(directory)/str(version)
                with local_manager(root,max_workers=1) as manager:
                    req=request(manager,version);base=manager.submit(req)['job_id']
                    self.assertEqual(wait(manager,base)['status'],'succeeded',manager.summary(base))
                    expected=manager.summary(base)['result'];expected_full=manager.result(base)['result']
                    plan=manager.plan(base);self.assertEqual(plan.schema_version,'openpine.run-plan.4');self.assertIsNotNone(plan.simulation_policy)
                    job=manager.submit(req,pause_after=1)['job_id'];paused(manager,job)
                    checkpoint=manager.services.checkpoints.manifest(manager.get(job)['checkpoint_id'])
                    broker=manager.objects.get(checkpoint['runtime_ref'])['simulation']['broker']
                    self.assertEqual((broker['count'],len(broker['pending']),broker['fill_count']),(1,1,0))
                with local_manager(root,max_workers=1) as manager:
                    manager.resume(job);state=wait(manager,job)
                    self.assertEqual(state['status'],'succeeded',state)
                    self.assertEqual(manager.summary(job)['result'],expected)
                    self.assertEqual(manager.result(job)['result'],expected_full)
                    cursor=0;events=[]
                    while cursor<expected['simulation']['journal']['records']:
                        page=manager.ledger(job,cursor,2);events+=page['events'];self.assertGreater(page['next'],cursor);cursor=page['next']
                    self.assertEqual(events,expected_full['simulation']['events'])

    def test_backup_restore_and_gc_keep_checkpoint_and_final_journals(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                req=request(manager);base=manager.submit(req)['job_id'];self.assertEqual(wait(manager,base)['status'],'succeeded')
                expected=manager.summary(base)['result'];job=manager.submit(req,pause_after=2)['job_id'];paused(manager,job)
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_services(root/'restored') as services:services.lifecycle.collect(apply=True,grace_seconds=0)
            with local_manager(root/'restored') as manager:
                manager.resume(job);self.assertEqual(wait(manager,job)['status'],'succeeded');self.assertEqual(manager.summary(job)['result'],expected)
                self.assertEqual(manager.result(job)['result']['simulation']['statistics']['equity'],'1003.72')

    def test_cli_settings_pause_resume_and_direct_result_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'strategy.pine';path.write_bytes(source(extra=',slippage=1').encode('utf-8'))
            bars=root/'bars.json';bars.write_bytes(canonical(asdict(data())))
            settings=BrokerSettings('0.5','0.1',2);policy=root/'broker.json';policy.write_bytes(canonical(asdict(settings)))
            command=[sys.executable,'-X','utf8','-m','apps.openpine','job','--state-dir',str(root/'live')]
            first=subprocess.run([*command,'--source',str(path),'--bars',str(bars),'--broker-settings',str(policy),'--pause-after','1'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr);job=json.loads(first.stdout)['job']['job_id']
            second=subprocess.run([*command,'--resume',job],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            result=json.loads(second.stdout)['response']['result']
            _,direct=Session(profile_id=HISTORICAL_PROFILE_ID).run(path.read_text(encoding='utf-8'),path.name,data=data(),broker_settings=settings)
            self.assertEqual(result,loads(canonical(asdict(direct))))
