"""Preparation is durable, complete and idempotent before any worker is published."""
import copy
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.launches import Launches
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import read_json
from packages.contracts import OpenPineError
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from packages.storage.lifecycle import reachable


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'state';self.open()
        self.primary=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/composite/bars.json')))
        self.higher=self.manager.market.save(input_snapshot(read_json(ROOT/'examples/composite/higher.json')))

    def open(self):
        self.manager=local_manager(self.directory)
        self.history=HistoryJobs(self.manager,start=False);self.launches=Launches(self.manager,self.history)
        self.addCleanup(self.close)

    def close(self):
        if self.manager is not None:
            self.history.close();self.manager.close();self.manager=None

    def config(self,version=6,family='macd-cross',**extra):
        name=f'{family}-v{version}.pine'
        ref=self.manager.freeze_input({'source':(ROOT/'examples/composite'/name).read_text(encoding='utf-8'),'source_name':name})
        return {'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':ref,
            'primary':{'kind':'snapshot','snapshot_id':self.primary},'contexts':[{'kind':'snapshot','snapshot_id':self.higher}],
            'inputs':{},'gap_policy':'fail','timeout_seconds':120,**extra}

    def finish(self,key,wanted='succeeded'):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            state=self.manager.get(key)
            if state['status']==wanted:return state
            if state['status'] in ('failed','cancelled','timed_out'):self.fail(state)
            time.sleep(.02)
        self.fail(state)

    def test_all_twelve_strategies_have_admitted_contexts_and_frozen_settings(self):
        for version in range(1,7):
            for family in ('macd-cross','bb-reversion'):
                config=self.config(version,family);state=self.launches.create(config)
                self.assertEqual(state['status'],'ready',state)
                self.assertEqual(self.launches.create(config)['prepared_ref'],state['prepared_ref'])
                self.assertEqual(state['prepared']['requirements']['language_version'],version)
                self.assertTrue(all(r['coverage']['requested_complete'] for r in state['prepared']['datasets']))
                self.assertEqual(len(state['base']['contexts']),1)
                self.assertIsNotNone(state['prepared']['request']['settings'])
        self.assertEqual(self.manager.list(),[])

    def test_graph_errors_reject_before_history_or_jobs_and_idempotency_conflicts(self):
        config=self.config();wrong={**config,'contexts':[]}
        with self.assertRaisesRegex(ValueError,'Missing contexts'):self.launches.create(wrong)
        wrong={**config,'contexts':[config['primary']]}
        with self.assertRaisesRegex(ValueError,'Ambiguous'):self.launches.create(wrong)
        self.launches.create(config)
        with self.assertRaisesRegex(ValueError,'idempotency'):self.launches.create({**config,'timeout_seconds':100})
        self.assertEqual(self.manager.list(),[])

    def test_gaps_require_explicit_policy_and_are_preserved_in_metadata(self):
        bars=read_json(ROOT/'examples/composite/bars.json');bars['bars'].pop(10)
        missing=self.manager.market.save(input_snapshot(bars));config=self.config();config['primary']['snapshot_id']=missing
        failed=self.launches.create(config);self.assertEqual(failed['status'],'failed');self.assertIn('Incomplete data',failed['error'])
        config.update(request_id=uuid.uuid4().hex,gap_policy='allow_with_metadata')
        ready=self.launches.create(config);self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['prepared']['datasets'][0]['coverage']['counts']['missing_candle'],1)
        self.assertEqual(self.manager.list(),[])

    def test_start_is_exactly_once_across_lost_link_and_joint_pause_resume(self):
        config=self.config(checkpoint_interval_bars=17,pause_after_bars=17)
        ready=self.launches.create(config);key=config['request_id'];job=self.launches.start(key)
        row=self.launches.repository.get(key)['state'];row.update(status='ready',job_id=None);self.launches.repository.save(row)
        self.assertEqual(self.launches.start(key)['job_id'],job['job_id'])
        self.finish(key,'paused');self.manager.resume(key);self.finish(key)
        result=self.manager.result(key)['result'];self.assertGreater(result['simulation']['statistics']['trades'],0)
        self.assertEqual(len(self.manager.list()),1)
        saved=self.launches.configuration(key);self.assertEqual(saved['source']['source_name'],'macd-cross-v6.pine')
        self.assertEqual(saved['request'],ready['prepared']['request'])

    def test_window_freezes_only_selected_ranges_and_independent_scoring(self):
        window={'warmup_start_ms':0,'score_start_ms':60*60000,'end_ms':200*60000}
        state=self.launches.create(self.config(window=window));self.assertEqual(state['status'],'ready',state)
        request=state['prepared']['request'];self.assertEqual(request['evaluation_window'],window)
        self.assertEqual(state['prepared']['datasets'][0]['identity']['count'],200)
        self.assertEqual(state['prepared']['datasets'][1]['identity']['count'],50)
        self.assertEqual(self.manager.market.manifest(self.primary)['identity']['count'],240)
        missing=read_json(ROOT/'examples/composite/bars.json');missing['bars']=missing['bars'][4:]
        config=self.config(window=window);config['primary']['snapshot_id']=self.manager.market.save(input_snapshot(missing))
        state=self.launches.create(config);self.assertEqual(state['status'],'failed');self.assertIn('Incomplete data',state['error'])

    def test_ready_preparation_survives_restart_backup_gc_and_pruning(self):
        state=self.launches.create(self.config());key=state['launch_id'];prepared=state['prepared_ref']
        self.launches.cancel(key);self.close();self.open()
        state=self.launches.resume(key);self.assertEqual(state['prepared_ref'],prepared)
        self.manager.db.execute('UPDATE datasets SET created_at=0');self.manager.db.execute('UPDATE pins SET created_at=0')
        self.manager.services.lifecycle.prune(time.time()-1,expire_inputs=True,expire_coverage=True,apply=True)
        self.assertIn(prepared,reachable(self.manager.db,self.manager.objects))
        destination=Path(self.temp.name)/'backup';backup(self.manager.db,self.directory,destination)
        self.close();restored=Path(self.temp.name)/'restored';restore(destination,restored)
        self.directory=restored;self.open();self.assertEqual(self.launches.status(key)['prepared_ref'],prepared)
        self.launches.start(key);self.finish(key)

    def test_unconfigured_program_can_warm_up_without_trading_before_score(self):
        source='//@version=6\nstrategy("window",default_qty_type=strategy.fixed)\nif bar_index==5\n    strategy.entry("early",strategy.long)\nif bar_index==70\n    strategy.entry("scored",strategy.long)\nplot(ta.ema(close,3))'
        config=self.config(window={'warmup_start_ms':0,'score_start_ms':60*60000,'end_ms':200*60000})
        config.update(input_ref=self.manager.freeze_input({'source':source,'source_name':'window.pine'}),contexts=[])
        ready=self.launches.create(config);self.assertEqual(ready['status'],'ready',ready)
        self.assertIsNone(ready['prepared']['plan']['settings'])
        job=self.launches.start(ready['launch_id']);self.finish(job['job_id'])
        result=self.manager.result(job['job_id'])['result']
        self.assertEqual(result['evaluation_window'],config['window'])
        self.assertEqual(result['simulation']['statistics']['fills'],1)
        with self.assertRaisesRegex(OpenPineError,'exactly one'):
            self.manager.submit({**ready['prepared']['request'],'inputs':{'unexpected':1}})
        configured=self.launches.create(self.config(window=config['window']))['prepared']['request']
        with self.assertRaisesRegex(OpenPineError,'Null execution settings'):
            self.manager.submit({**configured,'settings':None})

    def test_cancelling_preparation_does_not_cancel_shared_acquisition(self):
        from dataclasses import asdict
        from packages.contracts.market import HistoryRequest,InstrumentKey
        config=self.config();original=self.manager.objects.get(config['input_ref']);source=self.manager.objects.get(original['source_ref'])
        source['source']=source['source'].replace('TEST:TECHNICAL','BINANCE:BTCUSDT').replace('"4"','"5"');config['input_ref']=self.manager.freeze_input(source)
        for index,tf in enumerate(('1','5')):
            spec={'kind':'history','request':HistoryRequest(InstrumentKey.from_pine('BINANCE:BTCUSDT'),tf,0,14400000,'fail').payload()}
            if index:config['contexts']=[spec]
            else:config['primary']=spec
        first=self.launches.create(config);second=self.launches.create({**config,'request_id':uuid.uuid4().hex})
        self.assertEqual(first['sources'],second['sources']);self.launches.cancel(first['launch_id'])
        for source in second['sources']:self.assertEqual(self.history.status(source['task_id'])['status'],'queued')
        self.assertEqual(self.launches.status(second['launch_id'])['status'],'preparing')


if __name__=='__main__':unittest.main()
