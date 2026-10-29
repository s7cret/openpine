"""Pinned worker publications, exact scalar comparison and restart boundaries."""
import copy,tempfile,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.comparisons import Comparisons
from apps.openpine.traces import Traces
from apps.openpine.diagnostic_retention import DiagnosticRetention
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import digest,canonical
from packages.execution.host_memory import HostAdmission
from apps.openpine.compare_values import difference
from packages.storage.traces import get,put
from test_jobs import request,wait

class ComparisonFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'state';self.open()
        self.addCleanup(self.close)
    def open(self):
        self.manager=local_manager(self.directory,policy=ResourcePolicy(workers=1,memory_mb_per_worker=256,total_memory_mb=256),
            host_admission=HostAdmission(probe=lambda:None))
        self.service=Comparisons(self.manager,start=False);self.traces=Traces(self.manager,start=False)
        self.retention=DiagnosticRetention(self.manager,self.traces,self.service)
    def close(self):self.service.close();self.traces.close();self.manager.close()
    def job(self,v=6,source=None):
        payload=request(v)
        if source:payload['input']['source']=source
        key=self.manager.submit(payload)['job_id'];self.assertEqual(wait(self.manager,key)['status'],'succeeded');return key
    def compare(self,a,b,kind='job',mode='compatible',run=True):
        payload={'request_id':uuid.uuid4().hex,'left':{'kind':kind,'id':a},'right':{'kind':kind,'id':b},'mode':mode}
        value=self.service.create(payload);key=value['comparison_id']
        if run and value['state']['status']=='queued':self.service.launch(key,background=False)
        return self.service.status(key)

class ComparisonTests(ComparisonFixture):
    def test_wide_pinned_charts_have_a_row_budget_and_disjoint_bar_pages(self):
        from test_strategies import request as prepared_request,data
        from apps.openpine.comparison_chart import view
        source='//@version=6\nindicator("wide chart")\n'+'\n'.join(f'plot(close+{i})' for i in range(500))
        key=self.manager.submit(prepared_request(self.manager,text=source,bars=data(20)))['job_id']
        self.assertEqual(wait(self.manager,key)['status'],'succeeded',self.manager.result(key))
        comparison=self.compare(key,key);self.assertEqual(comparison['state']['status'],'equal')
        self.assertEqual(comparison['state']['checked']['rows'],10000)
        page=view(self.service,comparison['comparison_id'],start=0,limit=500)
        self.assertEqual(len(page['rows']),5000);self.assertEqual(page['next'],10);self.assertEqual({r['bar'] for r in page['rows']},set(range(10)))
        tail=view(self.service,comparison['comparison_id'],start=page['next'],limit=500)
        self.assertEqual(len(tail['rows']),5000);self.assertEqual(tail['next'],20);self.assertEqual({r['bar'] for r in tail['rows']},set(range(10,20)))
        self.assertEqual(view(self.service,comparison['comparison_id'],start=200,limit=500)['rows'],[])
    def test_exact_types_signed_zero_and_missing(self):
        for a,b in ((True,1),(1,1.0),(0.0,-0.0),(None,0),([1],[1,2]),({'a':1},{})):
            self.assertIsNotNone(difference(a,b),(a,b))
        self.assertIsNone(difference({'a':None,'x':[1,2.0]}, {'x':[1,2.0],'a':None}))
    def test_all_versions_equal_and_first_bar_difference(self):
        for v in range(1,7):
            a=self.job(v);normal=self.compare(a,a)
            self.assertEqual(normal['state']['status'],'equal',normal)
            self.assertEqual(normal['state']['checked']['rows'],12)
            declaration='study' if v<5 else 'indicator'
            b=self.job(v,f'//@version={v}\n{declaration}("changed")\nplot(close + 1)')
            changed=self.compare(a,b);self.assertEqual(changed['state']['status'],'different',changed)
            self.assertEqual(changed['state']['first_difference']['bar'],0)
            self.assertFalse(changed['report']['whole_program_equivalence_verified'])
    def test_keyed_submission_restart_and_stale_deletion_keep_shared_objects(self):
        a=self.job();value=self.compare(a,a,run=False);key=value['comparison_id'];config=value['configuration']
        self.assertEqual(self.service.create(config['request'])['configuration'],config)
        bad=copy.deepcopy(config['request']);bad['mode']='data_only'
        with self.assertRaises(ValueError):self.service.create(bad)
        self.service.control(key,'pause');report=self.retention.preview('comparison',key)
        self.close();self.open();self.service.launch(key,background=False)
        self.assertEqual(self.service.status(key)['state']['status'],'equal')
        with self.assertRaisesRegex(ValueError,'stale'):self.retention.delete('comparison',key,report['preview_id'])
        other=self.compare(a,a);preview=self.retention.preview('comparison',key)
        self.assertGreater(preview['bytes_released'],0)
        original=self.manager.summary(a);self.retention.delete('comparison',key,preview['preview_id'])
        self.assertEqual(self.manager.summary(a),original);self.assertEqual(self.service.status(other['comparison_id'])['state']['status'],'equal')
        with self.assertRaisesRegex(ValueError,'reserved'):self.service.create(config['request'])
        self.assertEqual(self.retention.policy(),{'keep_days':None});self.assertFalse(self.retention.candidates('comparison')['enabled'])
        self.retention.policy({'keep_days':1});self.assertEqual(self.retention.candidates('comparison')['items'],[])
    def test_resume_after_done_marker_and_midstream_pause(self):
        from apps.openpine.comparison_engine import compare
        a=self.job();value=self.compare(a,a,run=False);key=value['comparison_id']
        from apps.openpine.comparison_sources import read
        sides=[read(self.manager,d) for d in value['configuration']['sources']];state=value['state']
        state['cursor']={'channel':'done','index':0};state['status']='running'
        self.assertEqual(compare(self.manager,key,sides,state,lambda s:None,lambda:False)['status'],'equal')
        value=self.compare(a,a,run=False);key=value['comparison_id'];state=value['state'];initial=copy.deepcopy(state);seen=0
        def cancel():
            nonlocal seen;seen+=1;return seen==5
        with self.assertRaises(InterruptedError):compare(self.manager,key,sides,state,lambda s:None,cancel)
        self.assertEqual(state['checked']['rows'],4)
        completed=compare(self.manager,key,sides,state,lambda s:None,lambda:False)
        fresh=compare(self.manager,key,sides,initial,lambda s:None,lambda:False)
        self.assertEqual(completed['chains'],fresh['chains']);self.assertEqual(completed['checked']['rows'],12)
    def test_read_failure_and_disk_failure_are_not_equality(self):
        a=self.job();value=self.compare(a,a,run=False);key=value['comparison_id']
        with patch('packages.storage.comparison_readers.series_rows',side_effect=OSError('owned disk read fault')):self.service.launch(key,background=False)
        state=self.service.status(key);self.assertEqual(state['state']['status'],'failed');self.assertIsNone(state['report'])
        self.service.launch(key,background=False);self.assertEqual(self.service.status(key)['state']['status'],'equal')
    def test_program_ids_are_verified_and_user_ids_remain_significant(self):
        from packages.storage.comparison_ids import IdIndex
        with IdIndexContext(self.directory/'owned-ids.sqlite3',['a','b']) as index:
            for i in range(1500):
                values=[{'order_id':digest({'program':p,'kind':'order','sequence':i}),'entry_id':'same'} for p in ('a','b')]
                self.assertEqual(index.normalize(0,values[0]),index.normalize(1,values[1]))
            original=digest({'program':'a','kind':'order','sequence':0})
            self.assertEqual(index.normalize(0,{'target_id':original})['target_id'],['broker_id','order',0])
            self.assertNotEqual(index.normalize(0,{'entry_id':'A'}),index.normalize(1,{'entry_id':'B'}))
            with self.assertRaises(ValueError):index.normalize(0,{'order_id':'forged'})

class IdIndexContext:
    def __init__(self,*args):
        from packages.storage.comparison_ids import IdIndex
        self.index=IdIndex(*args)
    def __enter__(self):return self.index
    def __exit__(self,*args):self.index.close()

if __name__=='__main__':unittest.main()
