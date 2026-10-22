"""Durable finite groups, atomic publication, presets and shared resource admission."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import time
import unittest
import apsw
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from packages.contracts import OpenPineError
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import canonical,loads
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from test_inputs import DATA,code
from test_jobs import wait


def prepare(manager,version=6):
    ref=manager.freeze_input({'source':code(version),'source_name':f'v{version}.pine'})
    snapshot=manager.market.save(input_snapshot(loads(canonical(asdict(DATA)))))
    return {'name':'comparison','input_ref':ref,'snapshot_id':snapshot,'timeout_seconds':60}


def complete(service,identifier,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        service.tick();state=service.status(identifier)
        if state['status']!='running':return state
        time.sleep(.04)
    raise AssertionError(state)


class ExperimentTests(unittest.TestCase):
    def test_group_admission_limit_also_applies_to_resume(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            service=Experiments(manager,start=False);base={**prepare(manager),'variants':[{}]}
            groups=[service.create(base)['experiment_id'] for _ in range(16)]
            with self.assertRaises(ValueError):service.create(base)
            service.control(groups[0],'pause');service.create(base)
            with self.assertRaises(ValueError):service.control(groups[0],'resume')
            self.assertEqual(service.status(groups[0])['desired'],'paused');service.close()

    def test_real_coordinator_crash_and_backup_restore_keep_exactly_three_variants(self):
        from tools.experiment_soak import run
        with tempfile.TemporaryDirectory() as directory:
            result=run(Path(directory)/'proof')
            self.assertTrue(result['real_coordinator_kill']);self.assertFalse(result['completed_variant_repeated'])

    def test_validation_is_atomic_presets_are_revisioned_and_schema_scoped(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            service=Experiments(manager,start=False);base=prepare(manager)
            for extra in ({'grid':{'length':list(range(300))}},{'variants':[{'length':2},{'input:length':2}]},{'variants':[{'length':False}]},{'variants':[{'missing':1}]}):
                with self.assertRaises((OpenPineError,ValueError)):service.create({**base,**extra})
                self.assertEqual(service.list(),[]);self.assertEqual(manager.list(),[])
            preset={'input_ref':base['input_ref'],'name':'A','inputs':{'length':3},'expected_revision':None}
            first=service.save_preset(preset)
            with self.assertRaises(ValueError):service.save_preset(preset)
            second=service.save_preset({**preset,'expected_revision':1,'inputs':{'length':4}})
            self.assertEqual((first['revision'],second['revision']),(1,2))
            other=prepare(manager,5);self.assertEqual(service.presets(other['input_ref'])['presets'],[])
            group=service.create({**base,'variants':[{'length':2},{'length':3}]});identifier=group['experiment_id']
            with manager.lock:
                service.tick();first_job=service.status(identifier)['variants'][0]['job_id']
                self.assertEqual(service.repository.owned_active(),1)
                with self.assertRaises(apsw.ConstraintError):manager.db.execute('UPDATE experiment_variants SET job_id=NULL WHERE job_id=?',(first_job,))
                variant=service.repository.pending(identifier)
                service.repository.control(identifier,'paused')
                with self.assertRaises(ValueError):manager.enqueue_prepared(variant['request'],variant['plan'],job_id=variant['candidate_id'],experiment_variant=(identifier,1))
                self.assertEqual(len(manager.list()),1)
                self.assertIsNone(service.repository.pending(identifier)['job_id'])
            service.control(identifier,'cancel');self.assertEqual(service.status(identifier)['status'],'cancelled')
            self.assertEqual(manager.get(first_job)['status'],'cancelled')
            with self.assertRaises(ValueError):service.control(identifier,'resume')
            service.close()

    def test_backup_restart_keeps_completed_variant_and_unsubmitted_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);policy=ResourcePolicy(workers=1)
            with local_manager(root/'live',policy=policy) as manager:
                service=Experiments(manager,start=False);base=prepare(manager)
                group=service.create({**base,'grid':{'length':[2,3,4]},'checkpoint_interval_bars':1});identifier=group['experiment_id']
                service.tick();first=service.status(identifier)['variants'][0]['job_id']
                self.assertEqual(wait(manager,first)['status'],'succeeded',manager.get(first))
                saved=manager.get(first);manifest=service.repository.manifest(identifier)
                service.save_preset({'input_ref':base['input_ref'],'name':'retained','inputs':{'scale':2},'expected_revision':None})
                manager.services.lifecycle.prune(time.time(),expire_inputs=True,expire_coverage=True,apply=True)
                manager.services.lifecycle.collect(grace_seconds=0,apply=True)
                self.assertEqual(manager.market.manifest(base['snapshot_id'])['identity']['count'],6)
                backup(manager.db,manager.directory,root/'backup');service.close()
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored',policy=policy) as manager:
                service=Experiments(manager,start=False)
                self.assertEqual(service.repository.manifest(identifier),manifest)
                with patch('packages.execution.session.compile_source',side_effect=AssertionError('Recompiled a frozen experiment.')):
                    final=complete(service,identifier)
                self.assertEqual(final['status'],'succeeded',final)
                self.assertEqual(manager.get(first)['attempt_count'],saved['attempt_count'])
                self.assertEqual(manager.get(first)['result_id'],saved['result_id'])
                self.assertEqual(len(manager.list()),3)
                self.assertEqual(len({manager.request(v['job_id'])['input_ref'] for v in final['variants']}),1)
                self.assertEqual(len(service.presets(base['input_ref'])['presets']),1)
                service.close()

    def test_worker_kill_retries_owned_variant_and_close_preserves_remaining_work(self):
        with tempfile.TemporaryDirectory() as directory:
            policy=ResourcePolicy(workers=1)
            with local_manager(directory,policy=policy) as manager:
                service=Experiments(manager,start=False);group=service.create({**prepare(manager),'variants':[{'length':2},{'length':3}]});identifier=group['experiment_id']
                service.tick();job=service.status(identifier)['variants'][0]['job_id']
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    with manager.lock:
                        if job in manager.children:manager.children[job][0].kill();break
                    time.sleep(.01)
                else:self.fail('No worker to kill.')
                self.assertEqual(wait(manager,job)['status'],'succeeded',manager.get(job))
                self.assertEqual(manager.get(job)['attempt_count'],2)
                with manager.lock:
                    service.tick();remaining=service.status(identifier)['variants'][1]['job_id']
                service.close()
            with local_manager(directory,policy=policy) as manager:
                service=Experiments(manager,start=False)
                self.assertEqual(manager.get(remaining)['status'],'paused')
                self.assertEqual(complete(service,identifier)['status'],'succeeded')
                self.assertEqual(len(manager.list()),2);service.close()


if __name__=='__main__':unittest.main()
