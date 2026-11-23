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

    def test_foreign_signal_executes_on_the_primary_market_with_commission(self):
        folder = ROOT / 'examples/market-schedules'
        strategy = folder / 'overnight-strategy.pine'
        config = self.config(gap_policy='allow_with_metadata')
        config['input_ref'] = self.manager.freeze_input({'source': strategy.read_text(), 'source_name': strategy.name})
        primary = read_json(folder / 'strategy-bars.json')
        config['primary']['snapshot_id'] = self.manager.market.save(input_snapshot(primary))
        config['contexts'][0]['snapshot_id'] = self.manager.market.save(input_snapshot(read_json(folder / 'overnight.json')))
        config['fx_snapshot_id'] = self.manager.save_fx_rates(read_json(folder / 'rates.json'))
        uploaded_at = time.time()
        ready = self.launches.create(config)
        self.assertEqual(ready['status'], 'ready', ready.get('error'))
        key = config['request_id']
        self.launches.start(key)
        self.finish(key)
        result = self.manager.result(key)['result']
        fills = [event['value'] for event in result['simulation']['events'] if event['kind'] == 'fill']
        # A reversal closes the existing position before it opens the other side.
        expected = [(2, 1, '2', '102', '2.04'), (4, -1, '2', '104', '2.08'),
                    (4, -1, '1', '104', '1.04'), (10, 1, '1', '110', '1.1'),
                    (10, 1, '1', '110', '1.1'), (16, -1, '1', '116', '1.16')]
        self.assertEqual([(f['bar_index'], f['direction'], f['quantity'], f['price'], f['commission'])
                          for f in fills], expected)
        self.assertEqual([f['time'] for f in fills], [primary['data']['bars'][i]['time'] for i, *_ in expected])
        from decimal import Decimal
        cash, position = Decimal(1000), 0
        positions, equities = [], []
        for index, bar in enumerate(primary['data']['bars']):
            for at, side, quantity, price, fee in expected:
                if at == index:
                    signed = side * int(quantity)
                    position += signed
                    cash -= signed * Decimal(price) + Decimal(fee)
            positions.append(position)
            equities.append(float(cash + position * bar['close']))
        signal, position_series, equity_series = result['outputs']
        self.assertEqual(signal['values'], [None,5,5.5,6,6.5,10,10.5,None,11.5,12,*([None]*5),15,15.5,None,None,None])
        self.assertEqual(position_series['values'], positions)
        self.assertEqual(equity_series['values'], equities)
        self.assertEqual(cash, Decimal('995.48'))
        cancellations = [e['bar_index'] for e in result['simulation']['events'] if e['kind'] == 'cancelled']
        self.assertEqual(cancellations, [8])
        paused = dict(config, request_id=uuid.uuid4().hex, pause_after_bars=4)
        replay_key = paused['request_id']
        self.launches.create(paused)
        self.launches.start(replay_key)
        self.finish(replay_key, 'paused')
        self.manager.services.lifecycle.prune(uploaded_at, expire_inputs=True, apply=True)
        self.manager.services.lifecycle.collect(apply=True, grace_seconds=0)
        destination = Path(self.temp.name) / 'order-backup'
        backup(self.manager.db, self.directory, destination)
        self.close()
        self.directory = Path(self.temp.name) / 'recovered-orders'
        import errno
        from unittest.mock import patch
        def short_write(source, target):
            with source.open('rb') as incoming, target.open('wb') as outgoing:
                outgoing.write(incoming.read(32))
            raise OSError(errno.ENOSPC, 'test volume full during restore')
        with patch('packages.storage.backup.durable_copy', side_effect=short_write):
            with self.assertRaises(OSError) as failure:
                restore(destination, self.directory)
            self.assertEqual(failure.exception.errno, errno.ENOSPC)
        self.assertFalse(self.directory.exists())
        restore(destination, self.directory)
        self.open()
        self.assertEqual(self.manager.result(key)['result'], result)
        self.launches.start(replay_key)
        self.manager.resume(replay_key)
        self.finish(replay_key)
        self.assertEqual(self.manager.result(replay_key)['result'], result)

    def test_gaps_require_explicit_policy_and_are_preserved_in_metadata(self):
        bars=read_json(ROOT/'examples/composite/bars.json');bars['bars'].pop(10)
        missing=self.manager.market.save(input_snapshot(bars));config=self.config();config['primary']['snapshot_id']=missing
        failed=self.launches.create(config);self.assertEqual(failed['status'],'failed');self.assertIn('Incomplete data',failed['error'])
        config.update(request_id=uuid.uuid4().hex,gap_policy='allow_with_metadata')
        ready=self.launches.create(config);self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['prepared']['datasets'][0]['coverage']['counts']['missing_candle'],1)
        self.assertEqual(self.manager.list(),[])

    def test_scheduled_input_keeps_closures_separate_from_gaps_in_sliced_passport(self):
        minute=60000
        data=read_json(ROOT/'examples/composite/bars.json')
        schedule={'schema_version':'openpine.market-schedule.1','start_ms':0,'end_ms':240*minute,
                  'open_intervals':[[0,10*minute],[12*minute,240*minute]]}
        payload={'schema_version':'openpine.market-input.4','data':data,'market_schedule':schedule}
        with self.assertRaisesRegex(ValueError,'outside scheduled market hours'):
            input_snapshot(payload)
        del data['bars'][10:12]
        window={'warmup_start_ms':4*minute,'score_start_ms':8*minute,'end_ms':16*minute}
        config=self.config(window=window)
        config['primary']['snapshot_id']=self.manager.market.save(input_snapshot(payload))
        ready=self.launches.create(config)
        self.assertEqual(ready['status'],'ready',ready)
        dataset=ready['prepared']['datasets'][0]
        self.assertNotEqual(dataset['snapshot_id'],config['primary']['snapshot_id'])
        report=dataset['coverage']
        self.assertTrue(report['requested_complete'])
        self.assertEqual(report['counts']['market_closed'],2)
        self.assertEqual(report['bars'],10)
        validation=self.launches.validation(config['request_id'])['validation']
        self.assertEqual(validation['status'],'validated',validation)
        self.assertEqual(validation['datasets'][0]['coverage'],report)
        data['bars'].pop(11)
        config['request_id']=uuid.uuid4().hex
        config['primary']['snapshot_id']=self.manager.market.save(input_snapshot(payload))
        self.assertEqual(self.launches.create(config)['status'],'failed')
        config.update(request_id=uuid.uuid4().hex,gap_policy='allow_with_metadata')
        incomplete=self.launches.create(config)['prepared']['datasets'][0]['coverage']
        self.assertEqual(incomplete['counts']['market_closed'],2)
        self.assertEqual(incomplete['counts']['missing_candle'],1)

    def test_overnight_market_worker_matches_whole_and_partitioned_calendar(self):
        from packages.contracts import HISTORICAL_PROFILE_ID
        from packages.execution.artifacts import export_program
        from packages.execution.session import Session
        folder=ROOT/'examples/market-schedules'
        primary=read_json(folder/'primary.json')
        secondary=read_json(folder/'overnight.json')
        config=self.config()
        program=Session(profile_id=HISTORICAL_PROFILE_ID).compile((folder/'overnight.pine').read_text(),'overnight.pine')
        config['input_ref']=self.manager.freeze_input({'artifact':export_program(program)})
        config['primary']['snapshot_id']=self.manager.market.save(input_snapshot(primary))
        config['contexts'][0]['snapshot_id']=self.manager.market.save(input_snapshot(secondary))
        rates=read_json(folder/'rates.json')
        config['fx_snapshot_id']=self.manager.objects.put(rates)
        self.assertEqual(self.launches.create(config)['status'],'failed')
        config['gap_policy']='allow_with_metadata'

        def run(window=None, *, inputs=None):
            key=uuid.uuid4().hex
            request={**(config if inputs is None else inputs),'request_id':key}
            if window:request['window']=window
            ready=self.launches.create(request)
            self.assertEqual(ready['status'],'ready',ready)
            fx=ready['prepared']['requirements']['fx_snapshot']
            self.assertEqual(fx['snapshot_id'],request['fx_snapshot_id'])
            self.assertEqual(fx['source'],'fixed-calendar-example')
            self.assertEqual(fx['pairs'],[{'base':'USD','quote':'EUR','start_ms':1710028800000,
                                           'end_ms':1710374400000}])
            self.launches.start(key)
            self.finish(key)
            values=[o['values'] for o in self.manager.result(key)['result']['outputs']]
            return values,ready['prepared']['datasets'][1]['coverage'],key

        full,coverage,original_job=run()
        self.assertEqual(full,[list(range(100,120)),
                               [None,10,11,12,13,20,21,None,23,24,*([None]*5),30,31,None,None,None],
                               [22,23,0,1,3,*([23,0,1,2,3]*3)],
                               [None,5,5.5,6,6.5,10,10.5,None,11.5,12,*([None]*5),15,15.5,None,None,None]])
        self.assertEqual(coverage['counts']['missing_candle'],1)
        self.assertEqual(coverage['counts']['market_closed'],66)
        start=primary['market_schedule']['start_ms']
        split=primary['data']['bars'][10]['time']
        end=primary['market_schedule']['end_ms']
        left,lreport,_=run(dict(warmup_start_ms=start,score_start_ms=start,end_ms=split))
        right,rreport,_=run(dict(warmup_start_ms=split,score_start_ms=split,end_ms=end))
        self.assertEqual([a+b for a,b in zip(left,right,strict=True)],full)
        for reason,count in coverage['counts'].items():
            self.assertEqual(lreport['counts'][reason]+rreport['counts'][reason],count)
        holiday=dict(warmup_start_ms=split,score_start_ms=split,end_ms=split+5*3600000)
        holiday_values,hreport,holiday_job=run(holiday,inputs={**config,'gap_policy':'fail'})
        self.assertEqual(holiday_values,[series[10:15] for series in full])
        self.assertEqual(hreport['bars'],0)
        self.assertEqual(hreport['counts']['market_closed'],5)
        self.assertTrue(hreport['requested_complete'])
        validation=self.launches.validation(holiday_job)['validation']
        self.assertEqual(validation['status'],'validated',validation)
        self.assertEqual(validation['datasets'][1]['coverage'],hreport)
        carry_source=(folder/'overnight.pine').read_text().replace('barmerge.gaps_on','barmerge.gaps_off')
        carry=Session(profile_id=HISTORICAL_PROFILE_ID).compile(carry_source,'overnight-carry.pine')
        carry_ref=self.manager.freeze_input({'artifact':export_program(carry)})
        carried,_,_=run(holiday,inputs={**config,'input_ref':carry_ref,'gap_policy':'fail'})
        self.assertEqual(carried,[full[0][10:15],[24]*5,full[2][10:15],[12]*5])
        # Only the final overnight bar closes after the correction is published.
        day=86400000
        final_open=secondary['data']['bars'][-1]['time']
        midnight=final_open//day*day
        secondary['data']['bars'][-1]['close']=35
        rates['pairs'][0]['rates'].append(dict(day_ms=midnight-day,available_ms=final_open+1800000,rate=.75))
        revised={**config,'fx_snapshot_id':self.manager.objects.put(rates),
                 'contexts':[{'kind':'snapshot','snapshot_id':self.manager.market.save(input_snapshot(secondary))}]}
        saved=self.launches.configuration(original_job)['request']
        self.assertEqual(saved['fx_snapshot_id'],config['fx_snapshot_id'])
        self.assertNotEqual(saved['contexts'][0]['snapshot_id'],revised['contexts'][0]['snapshot_id'])
        job=self.manager.submit(saved)['job_id']
        self.finish(job)
        self.assertEqual([o['values'] for o in self.manager.result(job)['result']['outputs']],full)
        updated,_,_=run(inputs=revised)
        expected=copy.deepcopy(full)
        expected[1][16]=35
        expected[3][16]=26.25
        self.assertEqual(updated,expected)

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

    def test_indicator_warmup_and_checkpoint_at_first_sma_in_all_versions(self):
        from packages.contracts import HISTORICAL_PROFILE_ID
        from packages.execution.artifacts import export_program
        from packages.execution.session import Session
        compiler = Session(profile_id=HISTORICAL_PROFILE_ID)
        minute = 60_000
        data = read_json(ROOT / 'examples/pipeline/bars.json')
        data['bars'].append(dict(time=4*minute, open=49, high=51, low=48, close=50, volume=104))
        snapshot = self.manager.market.save(input_snapshot(data))

        def run(config, start, pause=False):
            key = uuid.uuid4().hex
            request = {**config, 'request_id': key,
                       'window': dict(warmup_start_ms=start, score_start_ms=2*minute, end_ms=5*minute)}
            if pause:
                request.update(checkpoint_interval_bars=2, pause_after_bars=2)
            ready = self.launches.create(request)
            self.assertEqual(ready['status'], 'ready', ready)
            self.launches.start(key)
            if pause:
                self.finish(key, 'paused')
                self.assertEqual(self.manager.get(key)['completed'], 2)
                self.assertTrue(self.manager.get(key)['checkpoint_id'])
                self.close()
                self.open()
                self.manager.resume(key)
            self.finish(key)
            chart = self.manager.chart(key, 0, 5)
            self.assertEqual([bar['time'] for bar in chart['bars']], list(range(start, 5*minute, minute)))
            first = self.manager.first_values(key)['first_bar_index']
            for output in chart['outputs']:
                self.assertEqual(first[output['output_id']], next((i for i, value in enumerate(output['values'])
                                                                  if value is not None), None))
            return [output['values'] for output in chart['outputs']]

        for version in range(1, 7):
            with self.subTest(version=version):
                declaration, ns = ('study', '') if version < 5 else ('indicator', 'ta.')
                source = (f'//@version={version}\n{declaration}("First values")\n'
                          f'plot({ns}sma(close,3), "SMA")\nplot({ns}ema(close,3), "EMA")\n'
                          f'plot({ns}sma(close,8), "Unseeded")\nplot(close-close, "Zero")\n')
                program = compiler.compile(source, f'first-v{version}.pine')
                config = dict(schema_version='openpine.launch-request.1', contexts=[], inputs={},
                              primary=dict(kind='snapshot', snapshot_id=snapshot), gap_policy='fail', timeout_seconds=120,
                              input_ref=self.manager.freeze_input({'artifact': export_program(program)}))
                cold = run(config, 2*minute)
                warm = run(config, 0)
                self.assertEqual(cold[0], [None, None, 40])
                self.assertEqual(warm[0], [None, None, 20, 30, 40])
                self.assertEqual(cold[1], cold[0] if version < 5 else [30, 35, 42.5])
                self.assertEqual(warm[1], warm[0] if version < 5 else [10, 15, 22.5, 31.25, 40.625])
                self.assertEqual(warm[2:], [[None]*5, [0]*5])
                self.assertEqual(run(config, 0, pause=True), warm)

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
