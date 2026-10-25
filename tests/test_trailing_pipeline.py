"""Independent relative/trailing prices through all versions and real workers."""
import copy,shutil,tempfile,unittest
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.strategy_ir import broker_policy,group_site_rules
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.marketdata import data_identity,from_payload
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.trailing_fixtures import CYCLES,TICK,bars,source,body,prepare
from tools.trailing_oracle import expected,cycle_totals,text
import test_environment_pipeline as environment

def request(manager,v=6,family='points',distance=3):
    root=ROOT/'examples/trailing'
    return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'{family}-v{v}.pine').read_text(),'source_name':f'{family}-v{v}.pine'}),
        'snapshot_id':manager.market.save(input_snapshot(read_json(root/f'{family}-input.json'))),'inputs':{'distance':distance},'timeout_seconds':120}

class TrailingPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_long_short_families_and_both_distances_match_closed_form(self):
        for v in range(1,7):
            for family,rows in CYCLES.items():
                for distance in (2,3):
                    for short in (False,True):
                        with self.subTest(v=v,family=family,distance=distance,short=short):
                            p,r=self.session.run(source(v,body(v,family,str(distance),False,short)),data=from_payload(bars(rows,short=short)),broker_settings=BrokerSettings.from_payload(TICK))
                            self.assertEqual(tuple(o.values for o in r.outputs),expected(family,distance,1,short))
                            self.assertEqual(r.simulation['schema_version'],'openpine.simulation.7');self.assertIn(f'openpine.trailing.v{v}.1',p.identity.semantic_profile_id.split('+'))
                            totals=cycle_totals(family,distance,short);stats=r.simulation['statistics']
                            for n in ('orders','fills','trades','closed_lots','closed_positions'):self.assertEqual(stats[n],totals[n])
                            self.assertEqual(stats['entry_commission'],text(totals['entry_fee']));self.assertEqual(stats['exit_commission'],text(totals['exit_fee']))
                            self.assertEqual(stats['gross_realized_exact'],text(totals['gross']));self.assertEqual(stats['exit_states'],[])

    def test_every_activation_extreme_pending_and_fill_boundary_roundtrips(self):
        for v in range(1,7):
            for family,rows in CYCLES.items():
                market=from_payload(bars(rows));p=self.session.compile(source(v,body(v,family,'3',False)));runtime=self.session.validate_program(p)
                policy=broker_policy(p,BrokerSettings.from_payload(TICK));baseline=self.session.execute(p,data=market,broker_settings=policy.settings)
                for cut in range(1,len(rows)+1):
                    with self.subTest(v=v,family=family,cut=cut):
                        def driver():return HistoricalDriver(runtime.prepare(p),MemorySource(market),simulation_policy=policy)
                        first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                        self.assertEqual(saved[0]['simulation']['broker']['schema_version'],'openpine.broker-state.7')
                        resumed=driver();resumed.restore(saved[0]);resumed.run();self.assertEqual(resumed.finish(data_identity(market)),baseline)

    def test_eighteen_native_workers_pin_tick_pause_resume_and_expose_bar_state(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            for v in range(1,7):
                for family in CYCLES:
                    with self.subTest(v=v,family=family):
                        cut=4 if family=='points' else 2
                        key=manager.submit(request(manager,v,family),pause_after=cut)['job_id'];environment.wait(manager,key,'paused');manager.resume(key)
                        state=environment.wait(manager,key);self.assertEqual(state['resumed_from_bar'],cut)
                        response=manager.result(key)['result'];self.assertEqual(tuple(tuple(o['values']) for o in response['outputs']),expected(family))
                        self.assertEqual(manager.plan(key).simulation_policy.settings.tick_size,'1')
                        chart=manager.chart(key,start=0,limit=8);self.assertEqual(chart['schema_version'],'openpine.chart-window.7');self.assertLessEqual(chart['protection']['event_bytes'],chart['protection']['event_byte_budget'])
                        if family=='points':
                            mark=next(e for e in chart['protection']['events'] if e['kind']=='mark' and e['bar_index']==3)
                            target=mark['value']['exit_states'][0]['targets'][0]
                            self.assertEqual((target['activation'],target['extreme'],target['effective_stop'],target['trail_stop'],target['stop_eligible_bar']),('105','112','107','109',4))
                            self.assertTrue(any(e['kind']=='trailing_activated' for e in chart['protection']['events']))

    def test_backup_restores_both_current_and_next_bar_stop(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with local_manager(root/'live') as manager:
                key=manager.submit(request(manager),pause_after=4)['job_id'];environment.wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                manager.resume(key);row=environment.wait(manager,key);self.assertEqual(row['resumed_from_bar'],4)
                result=manager.result(key)['result'];self.assertEqual(tuple(tuple(o['values']) for o in result['outputs']),expected('points'))

    def test_source_defaults_cannot_be_forged_into_additional_checkpoint_stops(self):
        from packages.simulation.broker.lots import LotBroker
        p=self.session.compile(source(6,body(6,'points','3',False)));market=from_payload(bars(CYCLES['points']));runtime=self.session.validate_program(p)
        policy=broker_policy(p,BrokerSettings.from_payload(TICK));driver=HistoricalDriver(runtime.prepare(p),MemorySource(market),simulation_policy=policy);saved=[]
        driver.run(pause_after=4,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        state=saved[0]['simulation']['broker'];candidate=copy.deepcopy(state)
        candidate['protections'][0]['intent']['stop']='97';candidate['exits']['orders'][0]['configuration']['stop']='97';candidate['exits']['orders'][0]['targets'][0]['stop']='97'
        new=LotBroker(policy,state['identity'])
        with self.assertRaises(ValueError):new.restore(candidate,count=4,last_time=180000,sites=group_site_rules(p))
        self.assertEqual(new.phase,'ready');self.assertEqual(new.exits.records,{})

    def test_metadata_absence_conflict_and_wrong_policy_version_fail_explicitly(self):
        from dataclasses import replace
        for v in range(1,7):
            p=self.session.compile(source(v,body(v,'relative','3',False)));data=from_payload(bars(CYCLES['relative']))
            with self.assertRaises(OpenPineError) as caught:self.session.execute(p,data=data)
            self.assertEqual((caught.exception.diagnostic.code,caught.exception.diagnostic.language_version),('EXIT_TICK_METADATA',v))
            snapshot=input_snapshot(read_json(ROOT/'examples/trailing/relative-input.json'))
            from packages.execution.environment import source_policy
            memory=MemorySource(data);memory.manifest={'provenance':loads(snapshot.provenance)}
            with self.assertRaises(OpenPineError):source_policy(p,memory,BrokerSettings(tick_size='0.5'))
        p=self.session.compile(source(1,body(1,'points','3',False)));runtime=self.session.validate_program(p);policy=broker_policy(p,BrokerSettings.from_payload(TICK))
        with self.assertRaises(ValueError):HistoricalDriver(runtime.prepare(p),MemorySource(from_payload(bars(CYCLES['points']))),simulation_policy=replace(policy,language_version=6))

    def test_legacy_program_profile_and_payload_readers_remain_selected(self):
        p=self.session.compile('//@version=6\nstrategy("Old",default_qty_type=strategy.fixed)\nstrategy.entry("L",strategy.long)\nplot(strategy.equity)')
        self.assertEqual(broker_policy(p).profile_id,'openpine.market-broker.1')
        from packages.contracts.trailing import TrailingPolicy
        from dataclasses import asdict
        from packages.contracts.trading import BrokerPolicy
        policy=TrailingPolicy('1000','2','1',0,BrokerSettings(tick_size='1'));self.assertEqual(BrokerPolicy.from_payload(asdict(policy)),policy)

    def test_independent_corpus_covers_every_version_and_negative_boundary(self):
        from tools.conformance import run_cases,TRAILING_CASES
        report=run_cases(cases_path=TRAILING_CASES);self.assertEqual((report['executed_programs'],report['expected_rejections']),(107,55))
        self.assertTrue(all('simulation.exit_extended' in report['operations_by_version'][str(v)] for v in range(1,7)))

    def test_release_examples_and_review_sources_are_reproducible(self):
        from tools.trailing_spec import prepare as release,RELEASE
        for name,raw in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)

    def test_repinning_schema_cannot_authorize_foreign_policy_abi_evidence_or_review(self):
        from tools.specification import encode,sha,load_snapshot,SpecError
        for change in ('policy','apis','evidence','review'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json');target=root/lock['trailing_release']['path'];value=read_json(target/'trailing.json')
                if change=='policy':value['versions']['1']['policy']['pair_priority']='unverified'
                elif change=='apis':value['versions']['1']['apis'][0][1]='simulation.exit'
                elif change=='evidence':value['versions']['1']['evidence']=value['versions']['6']['evidence']
                else:value['review']['sources'][0]['facts']['v6']='arbitrary rule'
                schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['trailing_policy']['properties'].update(versions={'const':value['versions']},review={'const':value['review']})
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (target/'trailing.json').write_bytes(encode(value));manifest=read_json(target/'manifest.json');manifest['files']['trailing.json']=sha((target/'trailing.json').read_bytes());(target/'manifest.json').write_bytes(encode(manifest))
                lock['trailing_release']['manifest_sha256']=sha((target/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError) as caught:load_snapshot(root)
                self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

    def test_observed_and_live_execution_keep_the_confirmed_only_boundary_all_versions(self):
        from packages.execution.realtime import ReplayEngine
        from packages.contracts.contexts import MarketContext
        for v in range(1,7):
            p=self.session.compile(source(v,body(v,'points','3',False)))
            with self.assertRaises(OpenPineError) as caught:ReplayEngine(self.session,p,(MarketContext('trailing','TEST:TRAIL','1','uploaded-file.1'),),'trailing',object())
            self.assertEqual((caught.exception.diagnostic.code,caught.exception.diagnostic.language_version),('PROTECTION_CONFIRMED_ONLY',v))

if __name__=='__main__':unittest.main()
