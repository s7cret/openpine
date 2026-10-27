"""Real admission pressure and the exact frozen research projection."""
import tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from apps.ui.server import Server
from apps.openpine.http_client import LocalClient
from test_prepared_requests_pipeline import prepare

class PreparationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=Server(('127.0.0.1',0),Path(self.temp.name));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=LocalClient(f'http://127.0.0.1:{self.server.server_port}');self.client.token=self.server.token
        self.raw,self.snapshots=prepare(self.server.jobs)
    def tearDown(self):self.server.shutdown();self.thread.join(10);self.server.server_close();self.temp.cleanup()
    def test_projection_uses_read_budget_while_another_preparation_is_active(self):
        with self.server.jobs.limiter.slot('prepare'):
            row=self.client.call('/api/launches/requirements',{'input_ref':self.raw['input_ref'],'inputs':{'sym':'TEST:B','tf':'1'}})
        self.assertEqual([(r['symbol'],r['timeframe']) for r in row['contexts']],[('TEST:B','1')]);self.assertEqual(self.server.jobs.list(),[])
    def test_busy_read_projection_retries_after_capacity_returns(self):
        ready=threading.Barrier(3);release=threading.Event();waits=[]
        def hold():
            with self.server.jobs.limiter.slot('read'):ready.wait(10);release.wait(15)
        threads=[threading.Thread(target=hold) for _ in range(2)]
        for thread in threads:thread.start()
        ready.wait(10);self.client.on_wait=lambda row:(waits.append(row),release.set())
        try:row=self.client.call('/api/launches/requirements',{'input_ref':self.raw['input_ref'],'inputs':{}})
        finally:
            release.set()
            for thread in threads:thread.join(10)
        self.assertTrue(waits);self.assertEqual(row['contexts'][0]['symbol'],'TEST:A');self.assertEqual(self.server.jobs.list(),[])
    def test_research_base_does_not_advance_or_mutate_preparation(self):
        m=self.server.jobs;config={'schema_version':'openpine.launch-request.1','request_id':'f'*32,'input_ref':self.raw['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':self.snapshots['parent']},'contexts':[{'kind':'snapshot','snapshot_id':self.snapshots['b1']}],
            'inputs':{'sym':'TEST:B','tf':'1'},'timeout_seconds':120,'gap_policy':'fail'}
        state=self.server.launches.create(config);self.assertEqual(state['status'],'ready')
        with patch.object(self.server.launches,'status',side_effect=AssertionError('Projection advanced preparation')):
            result=self.client.call('/api/launches/'+'f'*32+'/research-base',{'snapshots':[self.snapshots['a2']]})
        self.assertEqual(len(result['contexts']),2);self.assertEqual(m.list(),[])

if __name__=='__main__':unittest.main()
