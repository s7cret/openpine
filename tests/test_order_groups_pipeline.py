"""Independent all-version accounting, actual workers and complete group checkpoints."""
import copy,shutil,tempfile,unittest
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.marketdata import data_identity,from_payload
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.order_group_fixtures import CYCLES,bars,source,example_body,prepare
from tools.order_group_oracle import expected,cycle_totals,text
import test_environment_pipeline as environment


def request(manager,v=6,family='reduce',part=1):
    root=ROOT/'examples/order-groups'
    return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'{family}-v{v}.pine').read_text(),'source_name':f'{family}-v{v}.pine'}),
        'snapshot_id':manager.market.save(input_snapshot(read_json(root/f'{family}-bars.json'))),'inputs':{'part':part},'timeout_seconds':120}


class GroupPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_closed_form_positions_equity_prices_and_fee_accounting(self):
        for v in range(1,7):
            for family,rows in CYCLES.items():
                for part in (1,2):
                    with self.subTest(version=v,family=family,part=part):
                        p,r=self.session.run(source(v,example_body(v,family,repeat=False,part=str(part))),data=from_payload(bars(rows)))
                        self.assertEqual(tuple(o.values for o in r.outputs),expected(family,part,1))
                        self.assertEqual(broker_policy(p).profile_id,'openpine.order-group-broker.1')
                        self.assertIn(f'openpine.order-groups.v{v}.1',p.identity.semantic_profile_id.split('+'))
                        self.assertEqual(r.simulation['schema_version'],'openpine.simulation.6')
                        totals=cycle_totals(family,part);stats=r.simulation['statistics']
                        for name in ('orders','fills','trades','closed_lots','closed_positions'):self.assertEqual(stats[name],totals[name])
                        self.assertEqual(stats['entry_commission'],text(totals['entry_fee']));self.assertEqual(stats['exit_commission'],text(totals['exit_fee']))
                        self.assertEqual(stats['gross_realized_exact'],text(totals['gross']));self.assertEqual(stats['order_groups'],[])

    def test_every_activation_reduction_fill_and_close_all_boundary_roundtrips(self):
        for v in range(1,7):
            for family,rows in CYCLES.items():
                market=from_payload(bars(rows));p=self.session.compile(source(v,example_body(v,family,repeat=False,part='1')))
                runtime=self.session.validate_program(p);baseline=self.session.execute(p,data=market)
                for cut in range(1,len(rows)+1):
                    with self.subTest(version=v,family=family,cut=cut):
                        def driver():return HistoricalDriver(runtime.prepare(p),MemorySource(market),simulation_policy=broker_policy(p))
                        first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                        self.assertEqual(saved[0]['simulation']['broker']['schema_version'],'openpine.broker-state.6')
                        second=driver();second.restore(saved[0]);second.run();self.assertEqual(second.finish(data_identity(market)),baseline)

    def test_eighteen_native_workers_resume_and_expose_exact_groups_on_chart(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            for v in range(1,7):
                for family in CYCLES:
                    with self.subTest(version=v,family=family):
                        cut=4 if family=='reduce' else 2 if family=='cancel' else 3
                        key=manager.submit(request(manager,v,family),pause_after=cut)['job_id'];environment.wait(manager,key,'paused')
                        manager.resume(key);state=environment.wait(manager,key);self.assertEqual(state['resumed_from_bar'],cut)
                        r=manager.result(key)['result'];self.assertEqual(tuple(tuple(o['values']) for o in r['outputs']),expected(family,1))
                        self.assertEqual(manager.plan(key).simulation_policy.profile_id,'openpine.order-group-broker.1')
                        chart=manager.chart(key,start=0,limit=8);self.assertEqual(chart['schema_version'],'openpine.chart-window.6')
                        if family=='reduce':
                            marks=[e for e in chart['protection']['events'] if e['kind']=='mark' and e['bar_index']==3]
                            self.assertEqual(marks[0]['value']['order_groups'][0]['members'][0]['remaining_quantity'],'3')
                            self.assertEqual(marks[0]['value']['lots'][0]['quantity'],'4')
                            self.assertTrue(any(e['kind']=='oca_reduced' for e in chart['protection']['events']))

    def test_backup_with_activated_stop_limit_and_two_group_reservations(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with local_manager(root/'live') as manager:
                key=manager.submit(request(manager),pause_after=2)['job_id'];environment.wait(manager,key,'paused')
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                manager.resume(key);environment.wait(manager,key)
                self.assertEqual(manager.result(key)['result']['outputs'][1]['values'],list(expected('reduce',1)[1]))

    def test_legacy_header_can_select_order_profile_without_pyramiding(self):
        for v in range(1,7):
            long='true' if v==1 else 'strategy.long'
            s=f'//@version={v}\nstrategy("Legacy",default_qty_type=strategy.fixed)\nstrategy.order("A",{long},qty=1)\n'
            self.assertEqual(broker_policy(self.session.compile(s)).profile_id,'openpine.order-group-broker.1')
            ordinary=s.replace('strategy.order','strategy.entry')
            self.assertEqual(broker_policy(self.session.compile(ordinary)).profile_id,'openpine.market-broker.1')

    def test_named_when_is_versioned_and_new_quantities_report_actual_version(self):
        market=from_payload(bars(CYCLES['netting']))
        for v in range(1,7):
            long='true' if v==1 else 'strategy.long'
            s=source(v,f'strategy.order("A",{long},qty=-1)')
            with self.assertRaises(OpenPineError) as caught:self.session.run(s,data=market)
            self.assertEqual(caught.exception.diagnostic.language_version,v)
            when=source(v,f'strategy.order("A",{long},when=false)\nstrategy.close_all(when=false)')
            if v==6:
                with self.assertRaises(OpenPineError) as caught:self.session.compile(when)
                self.assertEqual(caught.exception.diagnostic.code,'UNKNOWN_ARGUMENT')
            else:
                _,r=self.session.run(when,data=market);self.assertEqual(r.simulation['statistics']['fills'],0)

    def test_independent_corpus_covers_all_operations_and_rejections_in_all_versions(self):
        from tools.conformance import run_cases,GROUP_CASES
        from packages.contracts.order_groups import OPERATIONS
        report=run_cases(cases_path=GROUP_CASES)
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(132,54))
        self.assertTrue(all(set(OPERATIONS)<=set(report['operations_by_version'][str(v)]) for v in range(1,7)))

    def test_release_and_examples_are_reproducible(self):
        from tools.order_group_spec import prepare as release,RELEASE
        for name,raw in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)

    def test_repinning_schema_does_not_authorize_foreign_abi_policy_or_evidence(self):
        from tools.specification import encode,sha,load_snapshot,SpecError
        for change in ('policy','apis','evidence'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json');target=root/lock['order_group_release']['path'];payload=read_json(target/'order-groups.json')
                if change=='policy':payload['versions']['1']['policy']['max_groups']=1000
                elif change=='apis':payload['versions']['1']['apis'][0][1]='simulation.entry'
                else:payload['versions']['1']['evidence']=payload['versions']['6']['evidence']
                schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['order_group_policy']['properties']['versions']={'const':payload['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (target/'order-groups.json').write_bytes(encode(payload));manifest=read_json(target/'manifest.json');manifest['files']['order-groups.json']=sha((target/'order-groups.json').read_bytes())
                (target/'manifest.json').write_bytes(encode(manifest));lock['order_group_release']['manifest_sha256']=sha((target/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError) as caught:load_snapshot(root)
                self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

    def test_observed_execution_requires_an_explicit_later_model(self):
        from packages.execution.realtime import ReplayEngine
        from packages.contracts.contexts import MarketContext
        for v in range(1,7):
            p=self.session.compile(source(v,example_body(v,'reduce',repeat=False,part='1')))
            with self.assertRaises(OpenPineError) as caught:ReplayEngine(self.session,p,(MarketContext('groups','TEST:GROUPS','1','uploaded-file.1'),),'groups',object())
            self.assertEqual(caught.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')


if __name__=='__main__':unittest.main()
