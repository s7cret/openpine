"""Actual HTTP metadata-only requirements and self-market frozen launch."""
import tempfile,threading,unittest,uuid
from pathlib import Path
from apps.ui.server import Server
from apps.openpine.http_client import LocalClient
from test_primary_targets_pipeline import prepare

class PrimaryTargetHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=Server(('127.0.0.1',0),Path(self.temp.name));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=LocalClient(f'http://127.0.0.1:{self.server.server_port}');self.client.token=self.server.token
    def tearDown(self):self.server.shutdown();self.thread.join(10);self.server.server_close();self.temp.cleanup()
    def test_pending_and_resolved_use_identical_ir_and_no_history_or_jobs(self):
        for v in (1,6):
            raw,ids=prepare(self.server.jobs,v)
            request={'input_ref':raw['input_ref'],'inputs':{}}
            pending=self.client.call('/api/launches/requirements',request);self.assertEqual(pending['status'],'needs_primary')
            ready=self.client.call('/api/launches/requirements',{**request,'primary_context':raw['primary_context']});self.assertEqual(ready['status'],'resolved')
            self.assertEqual((ready['context_graph']['primary']['symbol'],ready['context_graph']['primary']['timeframe']),('TEST:B','1'))
            self.assertEqual(self.server.jobs.list(),[]);self.assertEqual(self.server.history.repository.tasks(),[])

if __name__=='__main__':unittest.main()
