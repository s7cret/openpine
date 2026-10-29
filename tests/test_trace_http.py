"""HTTP and installed-style CLI exercise the same bounded diagnostic jobs."""
import tempfile,unittest,threading,time,uuid,json,subprocess,sys
from pathlib import Path
from apps.ui.server import Server
from packages.execution.host_memory import HostAdmission
from packages.contracts.storage import ResourcePolicy
from packages.compiler.semantic.specification import ROOT
from apps.openpine.http_client import LocalClient,LocalAPIError

class TraceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.server=Server(('127.0.0.1',0),Path(self.temp.name),policy=ResourcePolicy(workers=1,memory_mb_per_worker=256,total_memory_mb=256))
        self.server.daemon_threads=True
        self.server.jobs.limiter.host=HostAdmission(probe=lambda:{'total_bytes':16*1024**3,'available_bytes':8*1024**3,'source':'test.fixed'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close);self.url=f'http://127.0.0.1:{self.server.server_port}';self.client=LocalClient(self.url)
    def close(self):self.server.shutdown();self.thread.join(timeout=10);self.server.server_close()
    def prepare(self,v=6):
        ref=self.client.call('/api/compile',{'source':(ROOT/f'examples/pipeline/v{v}.pine').read_text(),'source_name':f'trace-v{v}.pine'})['input_ref']
        snapshot=self.client.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/pipeline/bars.json').read_text()})['sha256']
        key=uuid.uuid4().hex;self.client.call('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':key,'input_ref':ref,
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail'})
        return key
    def test_cli_http_worker_source_and_trace_agree(self):
        key=self.prepare();trace=uuid.uuid4().hex
        bootstrap="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
        result=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),'trace','run','--server',self.url,'--launch-id',key,
            '--id',trace,'--start-bar','1','--end-bar','3','--wait'],cwd=self.temp.name,capture_output=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(json.loads(result.stdout)['job']['status'],'succeeded')
        page=self.client.call(f'/api/traces/{trace}/events?limit=100');self.assertGreater(page['total'],0)
        self.assertEqual({e['primary_bar'] for e in page['events']},{1,2})
        source=self.client.call(f'/api/traces/{trace}/source');self.assertIn('Pipeline v6',source['source'])
        with self.assertRaises(LocalAPIError):self.client.call(f'/api/traces/{trace}/events?limit=1000')
    def test_stale_and_extra_fields_do_not_submit_diagnostic_work(self):
        key=self.prepare();payload={'request_id':uuid.uuid4().hex,'launch_id':key,'binding_sha256':'0'*64,'options':{}}
        with self.assertRaises(LocalAPIError):self.client.call('/api/traces',payload)
        self.assertEqual(self.server.jobs.repository.list(),[])
        payload['extra']=True
        with self.assertRaises(LocalAPIError):self.client.call('/api/traces',payload)

if __name__=='__main__':unittest.main()
