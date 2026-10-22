"""Native search jobs, holdout isolation, persistence, resource admission and recovery."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import apsw

from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from packages.contracts.storage import ResourcePolicy,RunPlan
from packages.contracts.wire import canonical,loads,digest
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from packages.storage.trades import event_rows
from test_inputs import DATA
from test_jobs import wait


def prepare(manager,version=6,*,strategy=False):
    expression='input.int(1)' if version>=5 else 'input(1)'
    declaration='strategy' if strategy else 'indicator' if version>=5 else 'study'
    source=f'//@version={version}\n{declaration}("research"'+(',default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1)' if strategy else ')')+f'\nn={expression}\n'
    if strategy:
        direction='strategy.long' if version>=3 else 'true'
        source+=f'if bar_index % 2 == 0\n    strategy.entry("L",{direction},qty=n)\nelse\n    strategy.close("L")\nplot(strategy.equity)'
    else:source+='plot(close*n)'
    ref=manager.freeze_input({'source':source,'source_name':f'search-v{version}.pine'})
    snapshot=manager.market.save(input_snapshot(loads(canonical(asdict(DATA)))))
    return {'name':'Research','input_ref':ref,'snapshot_id':snapshot,'timeout_seconds':60,
        'method':'grid','budget':3,'seed':42,'space':{'n':[1,2,3]},
        'objective':{'metric':'net_profit','direction':'maximize'} if strategy else {'metric':'output_mean','output_index':0,'direction':'maximize'},
        'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':60000,'end_ms':240000},
                   'holdout':{'warmup_start_ms':0,'score_start_ms':240000,'end_ms':360000}},'checkpoint_interval_bars':1}


def complete(service,timeout=150):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        service.tick();service.experiments.tick();states=service.list()
        if states and all(s['status'] not in ('selecting','checking_holdout') for s in states):return service.status(states[0]['search_id'])
        time.sleep(.04)
    raise AssertionError(states)


class SearchTests(unittest.TestCase):
    def test_native_all_versions_select_before_holdout_and_reuse_one_artifact(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,policy=ResourcePolicy(workers=1)) as manager:
            experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
            for version in range(1,7):
                with self.subTest(version=version):
                    base=prepare(manager,version);created=service.create(base);identifier=created['search_id']
                    with patch('packages.execution.session.compile_source',side_effect=AssertionError('Recompiled search input.')):state=complete(service)
                    self.assertEqual(state['status'],'succeeded',state)
                    self.assertEqual(state['decision']['winner_ordinal'],2)
                    self.assertEqual([r['assessment']['score'] for r in state['variants']],['12.0','24.0','36.0'])
                    self.assertEqual(state['holdout']['assessment']['score'],'43.5')
                    self.assertEqual(state['ranking'][0]['ordinal'],2)
                    self.assertEqual(len(manager.db.execute('SELECT 1 FROM jobs')),version*4)
                    artifacts={manager.summary(r['job_id'])['artifact_ref'] for r in state['variants']}
                    artifacts.add(manager.summary(state['holdout']['job_id'])['artifact_ref']);self.assertEqual(len(artifacts),1)
                    with self.assertRaises(ValueError):experiments.control(state['selection_id'],'pause')
                    self.assertEqual(service.status(identifier,start=1,limit=1)['variants'],state['variants'][1:2])
            service.close();experiments.close()

    def test_warmup_strategy_has_no_orders_and_metric_constraints_can_reject_all(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
            for version in range(1,7):
                base=prepare(manager,version,strategy=True);base['space']={'n':[1]};base['budget']=1
                base['windows']['selection']['score_start_ms']=120000
                identifier=service.create(base)['search_id'];state=complete(service)
                self.assertEqual(state['status'],'succeeded',state)
                summary=manager.summary(state['variants'][0]['job_id'])['result'];events=list(event_rows(manager.objects,summary['simulation']['journal']))
                orders=[e for e in events if e['kind']=='order'];self.assertEqual([e['time'] for e in orders],[120000,180000])
                self.assertEqual(summary['simulation']['statistics']['fills'],1)
                self.assertEqual(summary['simulation']['statistics']['position_size'],'1')
                # Entry at the next open (12), marked at close 13, fee 0.12: 0.88.
                self.assertEqual(state['variants'][0]['assessment']['score'],'0.88')
                self.assertEqual(state['variants'][0]['assessment']['metrics']['trades'],'0')
            second=service.create({**base,'constraints':[{'metric':'trades','operator':'gte','value':'1'}]})['search_id'];state=complete(service)
            self.assertEqual(state['search_id'],second);self.assertEqual(state['status'],'no_eligible_candidate')
            self.assertIsNone(state['holdout_job']);self.assertIsNone(state['decision']['winner_ordinal'])
            service.close();experiments.close()

    def test_window_checkpoint_resume_and_worker_kill_share_the_existing_budget(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,policy=ResourcePolicy(workers=1)) as manager:
            experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
            base=prepare(manager);base['budget']=1;base['space']={'n':[2]}
            identifier=service.create(base)['search_id'];selection=service.repository.get(identifier)['selection_id']
            row=experiments.repository.pending(selection)
            job=manager.enqueue_prepared(row['request'],row['plan'],job_id=row['candidate_id'],experiment_variant=(selection,0),pause_after=2)['job_id']
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                paused=manager.get(job)
                if paused['status'] in ('paused','succeeded','failed','cancelled','timed_out'):break
                time.sleep(.02)
            self.assertEqual(paused['status'],'paused',paused)
            checkpoint=manager.services.checkpoints.manifest(paused['checkpoint_id'],plan=row['plan'])
            from dataclasses import replace
            other=replace(row['plan'],evaluation_window={**row['plan'].evaluation_window,'score_start_ms':0})
            with self.assertRaises(ValueError):manager.services.checkpoints.manifest(paused['checkpoint_id'],plan=other)
            manager.resume(job);deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                with manager.lock:
                    if job in manager.children:manager.children[job][0].kill();break
                time.sleep(.01)
            else:self.fail('No actual search worker to kill.')
            state=complete(service);self.assertEqual(state['status'],'succeeded',state)
            self.assertGreaterEqual(manager.get(job)['attempt_count'],3)
            self.assertEqual(state['variants'][0]['assessment']['score'],'24.0')
            self.assertEqual(manager.get(job)['resumed_from_bar'],2)
            self.assertLessEqual(experiments.repository.owned_active(),1)
            self.assertEqual(len(manager.list()),2);service.close();experiments.close()

    def test_backup_restore_preserves_unsubmitted_holdouts_decision_and_completed_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);policy=ResourcePolicy(workers=1)
            with local_manager(root/'live',policy=policy) as manager:
                experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
                identifier=service.create(prepare(manager))['search_id'];experiments.tick()
                first=service.status(identifier)['variants'][0]['job_id'];self.assertEqual(wait(manager,first)['status'],'succeeded')
                service.tick();original=manager.get(first);sha=service.status(identifier)['manifest_sha256']
                service.control(identifier,'pause')
                manager.services.lifecycle.prune(time.time(),expire_inputs=True,expire_coverage=True,apply=True)
                manager.services.lifecycle.collect(grace_seconds=0,apply=True)
                backup(manager.db,manager.directory,root/'backup');service.close();experiments.close()
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored',policy=policy) as manager:
                experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
                self.assertEqual(service.status(identifier)['status'],'paused');service.control(identifier,'resume');state=complete(service)
                self.assertEqual(state['status'],'succeeded',state);self.assertEqual(state['manifest_sha256'],sha)
                self.assertEqual((manager.get(first)['result_id'],manager.get(first)['attempt_count']),(original['result_id'],original['attempt_count']))
                self.assertEqual(len(manager.list()),4)
                decision=service.repository.get(identifier)['decision'];service.tick();self.assertEqual(service.repository.get(identifier)['decision'],decision)
                rerun=service.rerun(identifier,2);experiments.tick();job=experiments.status(rerun['experiment_id'])['variants'][0]['job_id']
                self.assertEqual(wait(manager,job)['status'],'succeeded')
                self.assertEqual(manager.result(job)['result'],manager.result(state['variants'][2]['job_id'])['result'])
                with self.assertRaises(apsw.ConstraintError):manager.db.execute('UPDATE searches SET decision=NULL WHERE search_id=?',(identifier,))
                with self.assertRaises(apsw.ConstraintError):manager.db.execute('UPDATE search_holdouts SET ordinal=10 WHERE search_id=?',(identifier,))
                manager.db.check();service.close();experiments.close()

    def test_invalid_search_is_not_published_cancel_is_final_and_plans_pin_window(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False);base=prepare(manager)
            for change in ({'budget':2},{'objective':{'metric':'net_profit','direction':'maximize'}},{'space':{'n':[1,1]}}):
                with self.assertRaises(ValueError):service.create({**base,**change})
                self.assertEqual(service.list(),[]);self.assertEqual(experiments.list(),[]);self.assertEqual(manager.list(),[])
            identifier=service.create(base)['search_id'];row=experiments.repository.pending(service.repository.get(identifier)['selection_id'])
            plan=row['plan'];self.assertEqual(RunPlan.from_payload(plan.payload()),plan)
            self.assertEqual(plan.schema_version,'openpine.run-plan.6')
            with self.assertRaises(Exception):manager.enqueue_prepared({**row['request'],'evaluation_window':{**plan.evaluation_window,'score_start_ms':0}},plan)
            service.control(identifier,'cancel');self.assertEqual(service.status(identifier)['status'],'cancelled')
            with self.assertRaises(ValueError):service.control(identifier,'resume')
            service.close();experiments.close()


if __name__=='__main__':unittest.main()
