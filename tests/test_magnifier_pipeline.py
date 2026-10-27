"""Real historical workers, durable first-parent boundaries and storage roots."""
import tempfile,unittest,time
from dataclasses import asdict,replace
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.marketdata.providers import make_snapshot
from packages.contracts.wire import canonical,loads,digest
from packages.storage.results import ResultSink
from packages.storage.trades import event_rows
from packages.storage.checkpoints import CheckpointStore
from packages.storage.backup import backup,restore
from packages.runtime.engine import program_hash
from test_environment_pipeline import wait
from test_magnifier import datasets,config,source

def request(manager,v=6,*,reverse=False,missing='reject',text=None):
    a,b=datasets(reverse)
    lower=manager.market.save(make_snapshot(b,{'provider':'uploaded-file.1'}))
    return {'schema_version':'openpine.job-request.8','input_ref':manager.freeze_input({'source':text or source(v),'source_name':f'magnifier-v{v}.pine'}),
        'snapshot_id':manager.market.save(make_snapshot(a,{'provider':'uploaded-file.1'})),'inputs':{},'timeout_seconds':180,
        'magnifier':config(snapshot_id=lower,missing=missing,checkpoint_every_children=1)}

class MagnifierPipelineTests(unittest.TestCase):
    def test_real_workers_all_versions_pin_two_snapshots_and_resume_exact_journal(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            for v in range(1,7):
                raw=request(manager,v);first=manager.submit(raw,pause_after=1);key=first['job_id'];wait(manager,key,'paused')
                plan=manager.repository.plan(key);self.assertEqual(plan.schema_version,'openpine.run-plan.7')
                self.assertEqual(plan.magnifier['snapshot_id'],raw['magnifier']['snapshot_id'])
                manager.resume(key);wait(manager,key);saved=manager.result(key);self.assertEqual(saved['status'],'executed',saved)
                simulation=saved['result']['simulation'];self.assertEqual(simulation['statistics']['equity'],'1020')
                self.assertEqual(simulation['bar_magnifier']['covered_bars'],2)
                other=manager.submit(raw)['job_id'];wait(manager,other);out=manager.result(other)
                left=manager.ledger(key)['events'];right=manager.ledger(other)['events']
                self.assertEqual(left,right)
    def test_invalid_coverage_creates_no_job(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            raw=request(manager);a,b=datasets();gap=replace(b,bars=b.bars[:-1]);raw['magnifier']['snapshot_id']=manager.market.save(make_snapshot(gap,{'provider':'uploaded-file.1'}))
            with self.assertRaisesRegex(ValueError,'Incomplete'):manager.submit(raw)
            self.assertEqual(manager.db.execute('SELECT count(*) FROM jobs'),[(0,)])
    def test_first_parent_checkpoint_store_and_output_prefix_restore(self):
        from packages.execution.session import Session
        from packages.contracts import HISTORICAL_PROFILE_ID
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            raw=request(manager);frozen=manager.preparation.freeze(raw);plan=manager.preparation.build_plan(frozen,manager.policy)
            content=manager.objects.get(plan.input_ref);artifact=manager.objects.get(content['artifact_ref'])
            from packages.execution.artifacts import import_program
            program=import_program(artifact);session=Session(profile_id=HISTORICAL_PROFILE_ID)
            def driver(path):
                sink=ResultSink(manager.objects,path)
                d=session.stream_driver(program,manager.market.source(plan.snapshot_id),sink,simulation_policy=plan.simulation_policy,
                    settings=plan.settings,magnifier=plan.magnifier,lower_source=manager.market.source(plan.magnifier['snapshot_id']))
                return d,sink
            checkpoints=CheckpointStore(manager.objects);first,sink=driver(Path(directory)/'first');saved=[]
            first.run(checkpoint=lambda d:saved.append(checkpoints.save(d,sink,plan=plan,source=d.source,artifact_ref=content['artifact_ref'],program_sha256=program_hash(program))),pause=lambda:first.magnifier.observations==4)
            manifest,runtime,output=checkpoints.load(saved[-1],plan=plan,source=first.source)
            self.assertEqual(manifest['completed'],0);self.assertIsNone(manifest['last_time']);self.assertEqual(output['simulation_completed'],1)
            self.assertIsNone(output['simulation']['pending'])
            second,tail=driver(Path(directory)/'second');tail.restore(output,0);second.restore(runtime)
            tail.validate_simulation(second.simulation.broker,observation=second.magnifier.active);second.run()
            final=tail.finish(second.finish_stream(second.source.identity));self.assertEqual(final['simulation']['statistics']['equity'],'1020')
            self.assertEqual(final['observation_count'],2);sink.close();tail.close()
    def test_backup_and_gc_retain_lower_snapshot(self):
        with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory() as b,tempfile.TemporaryDirectory() as restored:
            with local_manager(d) as manager:
                raw=request(manager);key=manager.submit(raw)['job_id'];wait(manager,key)
                from packages.storage.lifecycle import Lifecycle
                lifecycle=Lifecycle(manager.db,manager.directory,manager.objects)
                pruned=lifecycle.prune(manager.get(key)['created_at'])
                self.assertNotIn(raw['magnifier']['snapshot_id'],pruned['datasets'])
                manager.market.manifest(raw['magnifier']['snapshot_id'])
                backup(manager.db,manager.directory,Path(b)/'backup')
            restore(Path(b)/'backup',Path(restored)/'restored')
            with local_manager(Path(restored)/'restored') as other:
                self.assertEqual(other.repository.plan(key).magnifier['snapshot_id'],raw['magnifier']['snapshot_id'])
                self.assertEqual(other.result(key)['result']['simulation']['statistics']['equity'],'1020')

    def test_worker_death_restores_published_cursor_inside_first_parent(self):
        from packages.contracts import HistoricalData
        from packages.contracts.storage import ResourcePolicy
        from test_magnifier import candle
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,policy=ResourcePolicy(max_attempts=2)) as manager:
            a=HistoricalData('primary','TEST:MAGNIFIER','1440',tuple(candle(i*86400000,100,100,100,100,1440) for i in range(2)),schema_version='openpine.historical-bars.2')
            b=HistoricalData('lower','TEST:MAGNIFIER','1',tuple(candle(i*60000,100,100,100,100) for i in range(2880)),schema_version='openpine.historical-bars.2')
            raw=request(manager);raw['snapshot_id']=manager.market.save(make_snapshot(a,{'provider':'uploaded-file.1'}))
            raw['magnifier']['snapshot_id']=manager.market.save(make_snapshot(b,{'provider':'uploaded-file.1'}))
            raw['magnifier']['checkpoint_every_children']=64
            key=manager.submit(raw)['job_id'];deadline=time.monotonic()+60;killed=False
            while time.monotonic()<deadline:
                state=manager.get(key)
                if state.get('checkpoint_id') and state['completed']==0:
                    with manager.lock:
                        child=manager.children.get(key)
                        if child is not None:child[0].kill();killed=True
                    break
                if state['status'] not in ('running','queued'):break
                time.sleep(.01)
            self.assertTrue(killed,state);state=wait(manager,key)
            self.assertEqual(state['attempt_count'],2)
            result=manager.summary(key)['result'];self.assertEqual(result['simulation']['bar_magnifier']['model_points'],11520)
            self.assertEqual(result['observation_count'],2)

    def test_search_and_walkforward_use_same_pinned_lower_source(self):
        from packages.contracts import HistoricalData
        from test_magnifier import candle
        from apps.openpine.experiments import Experiments
        from apps.openpine.optimization import Searches
        from apps.openpine.walkforward import WalkForwards
        from test_walkforward import drive
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            a=HistoricalData('primary','TEST:MAGNIFIER','2',tuple(candle(i*120000,100,100,100,100,2) for i in range(8)),schema_version='openpine.historical-bars.2')
            b=HistoricalData('lower','TEST:MAGNIFIER','1',tuple(candle(i*60000,100,100,100,100) for i in range(16)),schema_version='openpine.historical-bars.2')
            raw=request(manager,text=source(body='n=input.int(1)\nplot(strategy.equity+n)'))
            raw['snapshot_id']=manager.market.save(make_snapshot(a,{'provider':'uploaded-file.1'}));raw['magnifier']['snapshot_id']=manager.market.save(make_snapshot(b,{'provider':'uploaded-file.1'}))
            payload={k:raw[k] for k in ('input_ref','snapshot_id','timeout_seconds','magnifier')}
            payload.update(name='Magnifier folds',method='grid',budget=1,seed=42,space={'n':[1]},objective={'metric':'net_profit','direction':'maximize'},
                walkforward={'mode':'rolling','start_ms':120000,'train_ms':240000,'test_ms':120000,'step_ms':120000,'warmup_ms':120000,'folds':1,'max_jobs':2})
            experiments=Experiments(manager,start=False);searches=Searches(manager,experiments,start=False);studies=WalkForwards(manager,searches,start=False)
            key=studies.create(payload)['walkforward_id'];final=drive(studies,key);self.assertEqual(final['status'],'succeeded',final)
            plans=manager.db.execute('SELECT plan FROM run_plans')
            self.assertEqual(len(plans),2)
            for (encoded,) in plans:
                plan=loads(encoded);self.assertEqual(plan['magnifier'],raw['magnifier']);self.assertEqual(plan['schema_version'],'openpine.run-plan.7')

    def test_storage_migration_preserves_old_checkpoints_and_allows_first_parent_cursor(self):
        import apsw
        from packages.storage.database import Database,MIGRATIONS
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.sqlite';db=apsw.Connection(str(path))
            for sql in MIGRATIONS[:14]:db.execute(sql)
            db.pragma('user_version',14)
            db.execute("INSERT INTO jobs VALUES('job','paused',0,?, ?,NULL,NULL)",(b'{}',b'{}'))
            db.execute("INSERT INTO checkpoints VALUES('job','old',1,'attempt',0)");db.close()
            migrated=Database(path)
            try:
                self.assertEqual(migrated.execute('SELECT checkpoint_id,completed FROM checkpoints'),[('old',1)])
                migrated.execute("UPDATE checkpoints SET completed=0,checkpoint_id='inside-first'")
                migrated.check()
            finally:migrated.close()

if __name__=='__main__':unittest.main()
