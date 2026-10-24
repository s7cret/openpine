"""Protective Pine through IR, worker, journal, recovery and research services."""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_http as http_base
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from apps.openpine.research_reports import ResearchReports
from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,HistoricalBar
from packages.contracts.inputs import settings_for
from packages.contracts.protection import PROFILE
from packages.contracts.storage import ResourcePolicy
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.trading import BrokerPolicy
from packages.contracts.wire import canonical,loads,digest
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import read_bars,data_identity
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from test_jobs import wait
from test_checkpoints import paused

EXAMPLE=ROOT/'examples/protection'


def request(manager,version=6):
    return {'schema_version':'openpine.job-request.2','timeout_seconds':120,
        'input_ref':manager.freeze_input({'source':(EXAMPLE/f'v{version}.pine').read_text(encoding='utf-8'),'source_name':f'v{version}.pine'}),
        'snapshot_id':manager.market.save(input_snapshot(loads((EXAMPLE/'bars.json').read_text(encoding='utf-8'))))}


def search_request(req):
    return {'name':'Protective walk-forward','input_ref':req['input_ref'],'snapshot_id':req['snapshot_id'],
        'timeout_seconds':120,'space':{'stopPrice':[94.0,95.0]},'method':'grid','budget':2,'seed':0,
        'objective':{'metric':'net_profit','direction':'maximize'},
        'walkforward':{'mode':'rolling','start_ms':0,'train_ms':240000,'test_ms':240000,
            'step_ms':240000,'warmup_ms':0,'folds':2,'max_jobs':6}}


class ProtectionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_and_checkpoint_pending_bracket(self):
        bars=read_bars(EXAMPLE/'bars.json')
        for version in range(1,7):
            with self.subTest(version=version):
                text=(EXAMPLE/f'v{version}.pine').read_text(encoding='utf-8');program=self.session.compile(text)
                policy=broker_policy(program);self.assertEqual(policy.profile_id,PROFILE)
                self.assertEqual(BrokerPolicy.from_payload(asdict(policy)),policy)
                expected=self.session.execute(program,data=bars)
                def driver():return HistoricalDriver(self.session.validate_program(program).prepare(program,settings=settings_for(program,None)),MemorySource(bars),simulation_policy=policy)
                first=driver();saved=[];self.assertFalse(first.run(pause_after=1,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint())))))
                self.assertEqual(saved[0]['simulation']['broker']['schema_version'],'openpine.broker-state.2')
                second=driver();second.restore(saved[0]);second.run();actual=second.finish(data_identity(bars))
                self.assertEqual(actual,expected)
                stats=actual.simulation['statistics'];self.assertEqual((stats['equity'],stats['fills'],stats['trades']),('995',2,1))

    def test_bounded_signatures_and_forged_missing_level_origin(self):
        base=(EXAMPLE/'v6.pine').read_text(encoding='utf-8')
        for replacement in ['strategy.exit("X", "L", qty=1, profit=10)', 'strategy.exit("X", stop=95)',
                'strategy.exit("X", "L")','strategy.exit("X", "L", stop=95, when=true)',
                'strategy.cancel("X", when=true)','strategy.cancel_all(when=true)']:
            text=base[:base.index('if bar_index')]+replacement+'\n'
            with self.subTest(replacement=replacement),self.assertRaises(OpenPineError):self.session.compile(text)
        from dataclasses import replace
        program=self.session.compile(base);block=next(n for n in program.statements if hasattr(n,'body'))
        order=block.body[-1]
        forged=replace(order,arguments=tuple(replace(a,origin='default') if i in (2,3) else a for i,a in enumerate(order.arguments)))
        changed=replace(program,statements=tuple(replace(n,body=(*n.body[:-1],forged)) if n is block else n for n in program.statements))
        with self.assertRaises(OpenPineError):self.session.validate_program(changed)

    def test_legacy_when_and_observed_profiles_are_explicitly_bounded(self):
        for version in range(1,6):
            text=(ROOT/f'conformance/exits/v{version}-bracket.pine').read_text(encoding='utf-8').replace('stop=95, limit=110','stop=95, limit=110, when=false')
            _,result=self.session.run(text,data=read_bars(ROOT/'conformance/exits/bars.json'))
            self.assertEqual(result.simulation['statistics']['fills'],1)
        program=self.session.compile((EXAMPLE/'v6.pine').read_text(encoding='utf-8'))
        with self.assertRaises(OpenPineError) as error:ReplayEngine(self.session,program,(),'',object())
        self.assertEqual(error.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')


class ProtectionWorkerTests(unittest.TestCase):
    def test_real_coordinator_kills_at_pending_and_active_brackets(self):
        from tools.protection_soak import run
        with tempfile.TemporaryDirectory() as directory:
            for cut in ('pending','active'):
                with self.subTest(cut=cut):
                    result=run(Path(directory)/cut,cut)
                    self.assertEqual(result['status'],'passed');self.assertTrue(result['journal_and_parquet_identical'])

    def test_real_worker_kill_resumes_the_same_position_and_bracket(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2)) as manager:
            req=request(manager)
            req['snapshot_id']=manager.market.ingest({'context_id':'protected.crash','symbol':'TEST:PROTECTION','timeframe':'1','bar_schema':'openpine.historical-bars.2'},
                (HistoricalBar(i*60000,100.,100.,112. if i==29999 else 101.,94. if i==29999 else 99.,1.) for i in range(30000)),{'provider':'uploaded-file.1'})
            baseline=manager.submit(req,checkpoint_interval_bars=0)['job_id']
            self.assertEqual(wait(manager,baseline,timeout=180)['status'],'succeeded');expected=manager.summary(baseline)['result']
            job=manager.submit(req,checkpoint_interval_bars=1000)['job_id'];deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                with manager.lock:
                    state=manager.get(job)
                    if state['status']=='running' and state.get('checkpoint_id'):
                        manager.children[job][0].kill();break
                time.sleep(.01)
            else:self.fail('Worker did not publish a protective checkpoint while running.')
            state=wait(manager,job,timeout=180);self.assertEqual(state['status'],'succeeded',state)
            self.assertEqual(state['attempt_count'],2);self.assertGreaterEqual(state['resumed_from_bar'],1000)
            self.assertEqual(manager.summary(job)['result'],expected)

    def test_pause_backup_restore_reuses_frozen_ir_and_exact_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'state') as manager:
                req=request(manager)
                with patch('packages.execution.session.Session',side_effect=AssertionError('Coordinator recompiled frozen IR.')):
                    job=manager.submit(req)['job_id']
                self.assertEqual(wait(manager,job)['status'],'succeeded');expected=manager.summary(job)['result']
                job=manager.submit(req,pause_after=1,checkpoint_interval_bars=1)['job_id'];self.assertEqual(paused(manager,job)['status'],'paused')
                plan=manager.plan(job);self.assertEqual(plan.simulation_policy.profile_id,PROFILE)
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                manager.resume(job);state=wait(manager,job);self.assertEqual(state['status'],'succeeded',state)
                self.assertEqual(manager.summary(job)['result'],expected)
                self.assertEqual(state['resumed_from_bar'],1)
                chart=manager.chart(job,start=1,limit=2)
                self.assertEqual(chart['schema_version'],'openpine.chart-window.2');self.assertFalse(chart['protection']['truncated'])
                self.assertTrue(any(e['kind']=='protection_trigger' and e['value']['ambiguous'] for e in chart['protection']['events']))
                self.assertEqual([e['bar_index'] for e in chart['protection']['events'] if e['kind']=='mark'],[0,1,2])

    def test_walkforward_uses_protective_results_and_preserves_scoring(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
            identifier=studies.create(search_request(request(manager)))['walkforward_id'];deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                studies.tick();searches.tick();experiments.tick();state=studies.status(identifier)
                if state['status']!='running':break
                time.sleep(.04)
            self.assertEqual(state['status'],'succeeded',state)
            self.assertEqual([f['receipt']['decision']['winner_ordinal'] for f in state['folds']],[1,1])
            self.assertEqual([f['receipt']['holdout']['assessment']['score'] for f in state['folds']],['-5','-5'])
            report=ResearchReports(manager).create('walkforward',identifier)
            self.assertEqual(report['sha256'],digest(report['report']));self.assertEqual(len(manager.list()),6)
            for variant in report['report']['variants']:
                self.assertEqual(variant['plan']['simulation_policy']['profile_id'],PROFILE)
                self.assertEqual(variant['descriptive_metrics']['values']['closed_trades'],'1')
            studies.close();searches.close();experiments.close()


class ProtectionHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_base.HTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_base.HTTPTests.tearDownClass.__func__)
    call=classmethod(http_base.HTTPTests.call.__func__)
    finish=http_base.HTTPTests.finish

    def test_all_versions_chart_ledger_and_cli_http_equal(self):
        snapshot=self.call('/api/snapshots',{'provider':'upload','raw':(EXAMPLE/'bars.json').read_text(encoding='utf-8')})[1]['sha256']
        for version in range(1,7):
            path=EXAMPLE/f'v{version}.pine'
            code,compiled=self.call('/api/compile',{'source':path.read_text(encoding='utf-8'),'source_name':path.name})
            self.assertEqual(code,200,compiled)
            code,state=self.call('/api/jobs',{'schema_version':'openpine.job-request.2','input_ref':compiled['input_ref'],'snapshot_id':snapshot,'timeout_seconds':120})
            self.assertEqual(code,202,state);job=state['job_id'];self.assertEqual(self.finish(job)['status'],'succeeded')
            result=self.call(f'/api/jobs/{job}/result')[1]['result'];self.assertEqual(result['simulation']['statistics']['equity'],'995')
            chart=self.call(f'/api/jobs/{job}/chart?start=1&limit=2')[1]
            self.assertEqual(chart['schema_version'],'openpine.chart-window.2')
            events=self.call(f'/api/jobs/{job}/ledger?start=0&limit=100')[1]['events']
            self.assertEqual(sum(e['kind']=='fill' for e in events),2)
            self.assertEqual(sum(e['kind']=='protection_trigger' for e in chart['protection']['events']),1)
            if version in (1,6):
                with tempfile.TemporaryDirectory() as directory:
                    process=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','job','--source',str(path),'--bars',str(EXAMPLE/'bars.json'),'--state-dir',directory],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
                    self.assertEqual(process.returncode,0,process.stdout+process.stderr)
                    self.assertEqual(json.loads(process.stdout)['response']['result'],result)
        self.assertEqual(self.call('/protection.js')[0],200)
        self.assertIn(b'/protection.js',self.call('/')[1])


if __name__=='__main__':unittest.main()
