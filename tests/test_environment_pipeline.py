"""Pinned instruments through real workers, live warmup, replay and backup."""

import copy
from dataclasses import asdict
import hashlib
import shutil
from pathlib import Path
import tempfile
import time
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.environment_fixtures import prepare as fixtures
from tools.environment_spec import prepare,RELEASE
from tools.conformance import run_cases,ENVIRONMENT_CASES
from test_environment import source,INSTRUMENT
from test_realtime import PRIMARY,tape


def wait(manager,identifier,status='succeeded'):
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        row=manager.get(identifier)
        if row['status']==status:return row
        if row['status'] in ('succeeded','paused','failed','cancelled','timed_out'):raise AssertionError(row)
        time.sleep(.05)
    raise TimeoutError(row)


class EnvironmentPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_six_version_strategies_use_pinned_tick_and_resume_with_contexts(self):
        example=ROOT/'examples/environment'
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            snapshot=manager.market.save(input_snapshot(read_json(example/'input.json')))
            higher=manager.market.save(input_snapshot(read_json(example/'higher.json')))
            config=read_json(example/'contexts.json')
            for version in range(1,7):
                content={'source':(example/f'calendar-ticks-v{version}.pine').read_text(encoding='utf-8'),'source_name':f'calendar-v{version}.pine'}
                request={'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input(content),'snapshot_id':snapshot,
                    'primary_context':config['primary_context'],'contexts':[{'context':config['contexts'][0]['context'],'snapshot_id':higher}],
                    'timeout_seconds':120,'inputs':{'ticks':3}}
                identifier=manager.submit(request,pause_after=17)['job_id'];wait(manager,identifier,'paused')
                plan=manager.plan(identifier);self.assertEqual(plan.simulation_policy.settings.tick_size,'0.25')
                manager.resume(identifier);state=wait(manager,identifier);self.assertEqual(state['resumed_from_bar'],17)
                result=manager.result(identifier)['result'];self.assertEqual(result['outputs'][3]['values'],[.25]*360)
                self.assertEqual(result['outputs'][1]['values'],[1709157600000+i*60000 for i in range(360)])
                self.assertGreater(result['simulation']['statistics']['trades'],0)
                self.assertEqual(result['simulation']['policy']['settings']['tick_size'],'0.25')
                self.assertEqual(manager.chart(identifier,start=118,limit=7)['outputs'][1]['values'],result['outputs'][1]['values'][118:125])

    def test_new_snapshot_metadata_cannot_change_paused_job_and_broker_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            original=manager.market.save(input_snapshot(read_json(ROOT/'examples/environment/input.json')))
            script=source(6,'plot(syminfo.mintick)\nplot(barstate.islast ? 1:0)')
            request={'schema_version':'openpine.job-request.2','input_ref':manager.freeze_input({'source':script,'source_name':'frozen.pine'}),'snapshot_id':original,'timeout_seconds':60}
            identifier=manager.submit(request,pause_after=17)['job_id'];wait(manager,identifier,'paused')
            changed=manager.market.with_environment(original,{**INSTRUMENT,'mintick':'0.5'});self.assertNotEqual(changed,original)
            manager.resume(identifier);wait(manager,identifier);values=manager.result(identifier)['result']['outputs']
            self.assertEqual(values[0]['values'],[.25]*360);self.assertEqual(sum(values[1]['values']),1)
            second=manager.submit({**request,'snapshot_id':changed})['job_id'];wait(manager,second)
            self.assertEqual(manager.result(second)['result']['outputs'][0]['values'],[.5]*360)

    def test_managed_replay_environment_survives_backup_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=root/'store';script=source(6,'plot(time)\nplot(syminfo.mintick)\nplot(barstate.isconfirmed ? 1:0)')
            events=list(tape(3))
            with local_manager(store) as manager:
                service=Replays(manager);identifier=service.create({'input_ref':manager.freeze_input({'source':script,'source_name':'phases.pine'}),
                    'contexts':[asdict(PRIMARY)],'primary_id':'primary','broker_settings':None,'timeout_seconds':120,
                    'environments':{'primary':{'instrument':INSTRUMENT,'history_last_ms':None}}})['replay_id']
                service.append(identifier,[e.payload() for e in events]);service.advance(identifier,max_events=4,checkpoint_every=1)
                config=service.repository.config(identifier);self.assertEqual(config['environments']['primary']['instrument'],INSTRUMENT)
                backup(manager.db,manager.directory,root/'backup')
                service.advance(identifier);expected=service.view(identifier,'chart',0,20)
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                service=Replays(manager);service.advance(identifier,max_events=1,checkpoint_every=1);service.advance(identifier)
                self.assertEqual(service.view(identifier,'chart',0,20),expected)

    def test_live_freezes_catalogue_metadata_and_server_warmup_boundary(self):
        from test_live_market import prepare as live_prepare,message
        from packages.marketdata.catalog import normalize_exchange_info
        from test_marketdata import instrument_payload
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            history,live,identifier=live_prepare(manager,source=source(6,'plot(syminfo.mintick)\nplot(barstate.islastconfirmedhistory ? 1:0)\nplot(barstate.isrealtime ? 1:0)'))
            try:
                state=live.repository.session(identifier);replay=live.replays.repository.config(state['replay_id'])
                self.assertEqual(replay['environments']['primary']['history_last_ms'],180000)
                changed=instrument_payload();changed['symbols'][0]['filters'][0]['tickSize']='0.5'
                history.repository.replace_instruments(normalize_exchange_info(changed,999999))
                live.tick(identifier)
                for task in history.repository.tasks('queued'):history.process(task['task_id'])
                live.tick(identifier)
                state=live.repository.session(identifier);self.assertTrue(state['warmup_done'],state)
                live.hub.ingest(state['feed_id'],message(),received_at=400000);live.tick(identifier)
                preview=live.status(identifier)['replay']['preview']['outputs'];self.assertEqual([v['value'] for v in preview],[.01,0,1])
                chart=live.view(identifier,'chart',0,100)
                site=chart['outputs'][1]['output_id']
                self.assertEqual([r[3] for r in chart['rows'] if r[2]==site],[0,0,0,1])
            finally:live.close();history.close()

    def test_foreign_policy_references_and_evidence_rejected_even_after_full_repin(self):
        from tools.specification import load_snapshot,encode,sha,SpecError
        for change in ('policy','references','evidence'):
            with tempfile.TemporaryDirectory(prefix='openpine-environment-spec-') as temporary:
                root=Path(temporary);shutil.copytree(ROOT/'spec',root/'spec')
                lock=read_json(root/'spec/lock.json');directory=root/lock['environment_release']['path'];value=read_json(directory/'environment.json')
                if change=='policy':value['versions']['1']['policy']['time_close']='host_clock'
                elif change=='references':value['versions']['1']['references'][0]['slot']='environment.time_close'
                else:value['versions']['1']['evidence']=copy.deepcopy(value['versions']['6']['evidence'])
                schema=read_json(root/'spec/schema/import.schema.json')
                schema['$defs']['environment_policy']['properties']['versions']={'const':value['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (directory/'environment.json').write_bytes(encode(value));manifest=read_json(directory/'manifest.json')
                manifest['files']['environment.json']=sha((directory/'environment.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
                lock['environment_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError) as caught:load_snapshot(root)
                self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

    def test_specification_fixtures_and_all_old_bundles_are_unchanged(self):
        for name,raw in prepare().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        for name,raw in fixtures().items():self.assertEqual((ROOT/name).read_bytes(),raw)
        previous=read_json(ROOT/'docs/evidence/arrays-20261024.json')['source_files'];count=0
        for name,value in previous.items():
            if name.startswith('spec/bundles/'):
                self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),value,name);count+=1
        self.assertEqual(count,55)
        report=run_cases(cases_path=ENVIRONMENT_CASES)
        self.assertEqual((len(report['results']),report['executed_programs'],report['expected_rejections']),(81,63,18))


if __name__=='__main__':unittest.main()
