"""Atomic research receipts, real HTTP response loss, pressure and recovery."""
import concurrent.futures,copy,json,socket,tempfile,threading,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from apps.openpine.research_progress import progress
from apps.openpine.research_operations import lookup
from apps.openpine.http_client import LocalClient,LocalAPIError
from apps.ui.server import Server,Handler
from packages.storage.backup import backup,restore
from packages.storage.operations import OperationRepository
from packages.contracts import OpenPineError
from packages.contracts.wire import read_json
from packages.marketdata.providers import input_snapshot
from tools.specification import ROOT

def services(manager):
    a=Experiments(manager,start=False);b=Searches(manager,a,start=False);c=WalkForwards(manager,b,start=False)
    return {'experiment':a,'search':b,'walkforward':c}
def requests(manager):
    ref=manager.freeze_input({'source':(ROOT/'examples/search/v6.pine').read_text(),'source_name':'resilience.pine'})
    snapshot=manager.market.save(input_snapshot(read_json(ROOT/'examples/inputs/bars.json')))
    search={**read_json(ROOT/'examples/search/request.json'),'input_ref':ref,'snapshot_id':snapshot,'budget':1,'space':{'n':[1]}}
    experiment={k:search[k] for k in ('name','input_ref','snapshot_id','timeout_seconds')};experiment['variants']=[{'n':1}]
    walk={k:v for k,v in search.items() if k!='windows'};walk['walkforward']={'mode':'expanding','start_ms':60000,'train_ms':120000,'test_ms':60000,'step_ms':60000,'warmup_ms':60000,'folds':2,'max_jobs':4}
    return dict(experiment=experiment,search=search,walkforward=walk)

class ResiliencePipelineTests(unittest.TestCase):
    def test_all_three_receipts_survive_restart_backup_and_keep_identical_children(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);saved={}
            with local_manager(root/'live') as manager:
                ss=services(manager);pp=requests(manager)
                for kind,service in ss.items():
                    key=uuid.uuid4().hex;first=service.create(pp[kind],request_id=key);saved[kind]=(key,first)
                    self.assertEqual(first,service.create(pp[kind],request_id=key))
                    with self.assertRaises(OpenPineError) as caught:service.create({**pp[kind],'name':'changed'},request_id=key)
                    self.assertEqual(caught.exception.diagnostic.code,'IDEMPOTENCY_CONFLICT')
                counts=[manager.db.execute(f'SELECT COUNT(*) FROM {table}')[0][0] for table in ('experiments','searches','walkforwards','experiment_variants')]
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            for directory in ('live','restored'):
                with local_manager(root/directory) as manager:
                    ss=services(manager)
                    for kind,(key,receipt) in saved.items():self.assertEqual(ss[kind].create(pp[kind],request_id=key),receipt);self.assertEqual(lookup(manager,kind,key),receipt)
                    self.assertEqual(counts,[manager.db.execute(f'SELECT COUNT(*) FROM {table}')[0][0] for table in ('experiments','searches','walkforwards','experiment_variants')]);manager.db.check()
    def test_transaction_failure_never_leaves_half_composition_or_receipt(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            ss=services(manager);pp=requests(manager)
            for kind,service in ss.items():
                before=[manager.db.execute(f'SELECT COUNT(*) FROM {t}')[0][0] for t in ('experiments','experiment_variants','searches','walkforwards','research_operations')];key=uuid.uuid4().hex
                with patch.object(OperationRepository,'put',side_effect=OSError('simulated disk failure')),self.assertRaises(OSError):service.create(pp[kind],request_id=key)
                after=[manager.db.execute(f'SELECT COUNT(*) FROM {t}')[0][0] for t in ('experiments','experiment_variants','searches','walkforwards','research_operations')];self.assertEqual(before,after)
                self.assertIsNone(OperationRepository(manager.db).get(kind,key));self.assertEqual(service.create(pp[kind],request_id=key)['status'],'accepted')
    def test_parallel_identical_creates_have_one_resource(self):
        from packages.execution.limits import CoordinatorBusy
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            ss=services(manager);pp=requests(manager);key=uuid.uuid4().hex
            barrier=threading.Barrier(8)
            def submit(_):
                barrier.wait(5)
                try:return ss['experiment'].create(pp['experiment'],request_id=key)
                except CoordinatorBusy:return None
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                values=list(pool.map(submit,range(16)))
            first=ss['experiment'].create(pp['experiment'],request_id=key)
            self.assertTrue(any(v is not None for v in values));self.assertTrue(all(v is None or v==first for v in values));self.assertEqual(manager.db.execute('SELECT COUNT(*) FROM experiments'),[(1,)])
    def test_process_death_after_commit_recovers_key_without_republishing(self):
        import os,subprocess,sys
        from packages.contracts.wire import atomic_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);key=uuid.uuid4().hex
            with local_manager(root/'live') as manager:payload=requests(manager)['search']
            atomic_json(root/'request.json',payload)
            code="import os,sys;from pathlib import Path;sys.path.insert(0,str(Path.cwd()/'tests'));from test_resilience_pipeline import services;from apps.openpine.bootstrap import local_manager;from packages.contracts.wire import read_json;m=local_manager(sys.argv[1]);services(m)['search'].create(read_json(sys.argv[2]),request_id=sys.argv[3]);os._exit(23)"
            child=subprocess.run([sys.executable,'-X','utf8','-c',code,str(root/'live'),str(root/'request.json'),key],cwd=ROOT,capture_output=True,text=True,timeout=60)
            self.assertEqual(child.returncode,23,child.stdout+child.stderr)
            with local_manager(root/'live') as manager:
                service=services(manager)['search']
                with patch.object(service,'prepare',side_effect=AssertionError('Recreated accepted study.')):receipt=service.create(payload,request_id=key)
                self.assertEqual(lookup(manager,'search',key),receipt);self.assertEqual(manager.db.execute('SELECT COUNT(*) FROM searches'),[(1,)])
    def test_small_progress_and_controls_work_while_heavy_slots_are_full(self):
        from apps.ui.operation_routes import control
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            ss=services(manager);pp=requests(manager);accepted={k:s.create(pp[k],request_id=uuid.uuid4().hex) for k,s in ss.items()}
            entered=[threading.Event(),threading.Event()];release=threading.Event()
            def hold(kind,event):
                with manager.limiter.slot(kind):event.set();release.wait(10)
            threads=[threading.Thread(target=hold,args=(kind,event)) for kind,event in zip(('prepare','read'),entered)]
            for t in threads:t.start()
            self.assertTrue(all(e.wait(5) for e in entered));handler=SimpleNamespace(headers={'X-OpenPine-Control-View':'progress'},server=SimpleNamespace(jobs=manager,experiments=ss['experiment'],searches=ss['search'],walkforwards=ss['walkforward']))
            try:
                with patch.object(manager.objects,'get',side_effect=AssertionError('Metadata opened an object.')):
                    for kind,receipt in accepted.items():
                        self.assertEqual(progress(manager,kind,receipt['resource_id'])['name'],pp[kind]['name'])
                        self.assertEqual(control(handler,kind,receipt['resource_id'],'pause')['desired'],'paused')
                        self.assertEqual(lookup(manager,kind,receipt['request_id']),receipt)
            finally:
                release.set()
                for t in threads:t.join(5)
    def test_deleted_receipt_is_a_tombstone_and_corruption_is_rejected(self):
        from apps.openpine.research_maintenance import ResearchMaintenance
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            service=services(manager)['experiment'];payload=requests(manager)['experiment'];key=uuid.uuid4().hex;receipt=service.create(payload,request_id=key);identifier=receipt['resource_id']
            service.control(identifier,'cancel');maintenance=ResearchMaintenance(manager);preview=maintenance.preview('experiment',identifier);maintenance.apply('experiment',identifier,preview['preview_id'])
            with self.assertRaises(OpenPineError) as caught:service.create(payload,request_id=key)
            self.assertEqual(caught.exception.diagnostic.code,'OPERATION_GONE');OperationRepository(manager.db).audit()
            manager.db.execute('DROP TRIGGER immutable_research_operations');manager.db.execute("UPDATE research_operations SET receipt_sha256=?",('0'*64,))
            with self.assertRaises(ValueError):OperationRepository(manager.db).audit()
    def test_missing_target_without_deletion_marker_is_corruption(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
            receipt=services(manager)['experiment'].create(requests(manager)['experiment'],request_id=uuid.uuid4().hex)
            manager.db.execute('DELETE FROM experiment_variants WHERE experiment_id=?',(receipt['resource_id'],));manager.db.execute('DELETE FROM experiments WHERE experiment_id=?',(receipt['resource_id'],))
            with self.assertRaisesRegex(ValueError,'deletion marker'):OperationRepository(manager.db).audit()
    def test_real_http_lost_creation_response_never_duplicates_a_study(self):
        with tempfile.TemporaryDirectory() as d:
            server=Server(('127.0.0.1',0),d);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=LocalClient(f'http://127.0.0.1:{server.server_port}');payload=requests(server.jobs)['walkforward'];key=uuid.uuid4().hex;original=Handler.send;lost=[]
                def send(handler,status,value,**kwargs):
                    if status==201 and type(value) is dict and value.get('request_id')==key and not lost:
                        lost.append(value);handler.close_connection=True;handler.connection.shutdown(socket.SHUT_RDWR);handler.connection.close();return
                    return original(handler,status,value,**kwargs)
                with patch.object(Handler,'send',send):receipt=client.call('/api/walkforwards',payload,request_id=key)
                self.assertEqual(lost,[receipt]);self.assertEqual(client.call('/api/operations/walkforward/'+key),receipt);self.assertEqual(server.jobs.db.execute('SELECT COUNT(*) FROM walkforwards'),[(1,)])
                from http.client import HTTPConnection
                connection=HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                try:
                    connection.request('POST','/api/walkforwards/'+receipt['resource_id']+'/pause',body=b'{}',headers={'Content-Type':'application/json','X-OpenPine-Token':server.token})
                    legacy=connection.getresponse();self.assertEqual(legacy.status,200);self.assertIn('folds',json.loads(legacy.read()))
                finally:connection.close()
                light=client.call('/api/walkforwards/'+receipt['resource_id']+'/pause',{});self.assertEqual(light['schema_version'],'openpine.research-progress.1');self.assertNotIn('folds',light)
                with self.assertRaises(LocalAPIError) as caught:client.call('/api/walkforwards',{**payload,'name':'other'},request_id=key)
                self.assertEqual(caught.exception.status,409);self.assertEqual(caught.exception.response['diagnostics'][0]['code'],'IDEMPOTENCY_CONFLICT')
            finally:server.shutdown();thread.join(10);server.server_close()
    def test_v13_migration_is_atomic_and_preserves_prior_bytes(self):
        import apsw
        from packages.storage.database import Database,MIGRATIONS
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'catalog.sqlite3';db=apsw.Connection(str(path))
            for migration in MIGRATIONS[:13]:db.execute(migration)
            db.execute("INSERT INTO settings VALUES('preserve',?)",(b'old bytes',));db.pragma('user_version',13);db.close()
            broken=(*MIGRATIONS[:13],MIGRATIONS[13]+'\n INVALID SQL;')
            with patch('packages.storage.database.MIGRATIONS',broken),self.assertRaises(apsw.SQLError):Database(path)
            db=apsw.Connection(str(path));self.assertEqual(db.pragma('user_version'),13);db.close()
            db=Database(path)
            try:self.assertEqual(db.execute("SELECT value FROM settings WHERE name='preserve'"),[(b'old bytes',)]);self.assertEqual(db.connection.pragma('user_version'),14);db.check()
            finally:db.close()
    def test_reserved_connections_keep_progress_and_pause_available(self):
        from packages.contracts.storage import CoordinatorPolicy
        with tempfile.TemporaryDirectory() as d:
            server=Server(('127.0.0.1',0),d,coordinator_policy=CoordinatorPolicy(memory_mb=2048,readers=16));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}';client=LocalClient(base);payload=requests(server.jobs)['experiment'];receipt=client.call('/api/experiments',payload,request_id=uuid.uuid4().hex)
            entered=threading.Event();release=threading.Event();lock=threading.Lock();active=[];original=server.store.export
            def export(identifier):
                with lock:
                    active.append(identifier)
                    if len(active)==6:entered.set()
                if not release.wait(10):raise RuntimeError('Test export was not released.')
                return original(identifier)
            try:
                with patch.object(server.store,'export',export),concurrent.futures.ThreadPoolExecutor(6) as pool:
                    futures=[pool.submit(client.call,'/api/snapshots/'+payload['snapshot_id']) for _ in range(6)]
                    try:
                        self.assertTrue(entered.wait(8))
                        with self.assertRaises(LocalAPIError) as caught:LocalClient(base,retry_attempts=1).call('/api/snapshots/'+payload['snapshot_id'])
                        self.assertEqual(caught.exception.response['admission']['reason'],'connections')
                        path='/api/experiments/'+receipt['resource_id'];self.assertEqual(client.call(path+'/progress')['experiment_id'],receipt['resource_id'])
                        self.assertEqual(client.call(path+'/pause',{})['desired'],'paused')
                    finally:release.set()
                    self.assertTrue(all(f.result(timeout=10)['data']['bars'] for f in futures))
            finally:release.set();server.shutdown();thread.join(10);server.server_close()
    def test_client_pending_receipt_recovers_after_server_restart(self):
        from types import SimpleNamespace
        from apps.openpine.http_client import Submission
        from packages.contracts.wire import atomic_json
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);server=Server(('127.0.0.1',0),root/'server');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();port=server.server_port;base=f'http://127.0.0.1:{port}'
            payload=requests(server.jobs)['experiment'];request=root/'request.json';atomic_json(request,payload)
            args=SimpleNamespace(operation_file=root/'client.json',request=request,source=None,bars=None,output=root/'out.json',request_id=None,new_request=False,server=base)
            original=Handler.send;lost=[]
            def send(handler,status,value,**kwargs):
                if status==201 and type(value) is dict and value.get('schema_version')=='openpine.research-operation.1' and not lost:
                    lost.append(value);handler.close_connection=True;handler.connection.shutdown(socket.SHUT_RDWR);handler.connection.close();return
                return original(handler,status,value,**kwargs)
            def interrupt(_):raise KeyboardInterrupt()
            try:
                client=LocalClient(base,on_wait=interrupt);submission=Submission(args,'experiment',payload,client)
                with patch.object(Handler,'send',send),self.assertRaises(KeyboardInterrupt):submission.submit()
                self.assertEqual(read_json(args.operation_file)['status'],'pending');self.assertEqual(len(lost),1)
            finally:server.shutdown();thread.join(10);server.server_close()
            server=Server(('127.0.0.1',port),root/'server');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch.object(server.experiments,'prepare',side_effect=AssertionError('Duplicated study.')):
                    recovered=Submission(args,'experiment',payload,LocalClient(base)).submit()
                self.assertEqual(recovered,lost[0]);self.assertEqual(server.jobs.db.execute('SELECT COUNT(*) FROM experiments'),[(1,)])
            finally:server.shutdown();thread.join(10);server.server_close()

if __name__=='__main__':unittest.main()
