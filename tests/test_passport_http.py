"""Legacy requirements stay stable; explanations are opt-in read projections."""
import json,subprocess,sys,tempfile,threading,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict
from apps.ui.server import Server
from packages.contracts.contexts import MarketContext
from packages.contracts.wire import read_json,loads,canonical
from packages.compiler.semantic.specification import ROOT
from tools.acceptance_http import request_json

class PassportHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.work=Path(cls.temp.name)
        cls.server=Server(('127.0.0.1',0),cls.work/'state');cls.thread=threading.Thread(target=cls.server.serve_forever);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.thread.join(10);cls.server.server_close();cls.temp.cleanup()
    def call(self,path,payload=None):return request_json(self.base,self.server.token,path,payload)
    def compiled(self,version=6):return self.call('/api/compile',{'source':(ROOT/f'examples/pipeline/v{version}.pine').read_text(),'source_name':'owned.pine'})
    def payload(self,compiled,**extra):return {'input_ref':compiled['input_ref'],'inputs':{},'primary_context':asdict(MarketContext('primary','TEST:PIPELINE','1','metadata.1')),**extra}

    def test_source_ir_cli_http_parity_all_versions_without_workers_or_fetch(self):
        for v in range(1,7):
            c=self.compiled(v);payload=self.payload(c)
            legacy=self.call('/api/launches/requirements',payload)
            with patch.object(self.server.history,'submit',side_effect=AssertionError('no acquisition')):
                value=self.call('/api/launches/requirements',{**payload,'explain':True})
            report=value.pop('passport');raw=value.pop('passport_json')
            self.assertEqual(legacy,value);self.assertEqual(canonical(report),canonical(loads(raw)))
            artifact=self.work/f'v{v}.json';primary=self.work/'primary.json'
            artifact.write_text(json.dumps(c['artifact']),encoding='utf-8');primary.write_text(json.dumps(payload['primary_context']),encoding='utf-8')
            process=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','passport','--artifact',str(artifact),'--primary-context',str(primary)],cwd=ROOT,capture_output=True,timeout=60)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(canonical(loads(process.stdout)),canonical(report))
        self.assertEqual(self.server.jobs.list(),[])

    def test_raw_download_preserves_input_float_lexeme(self):
        text='//@version=6\nindicator("input")\nn=input.float(1.0)\nplot(n)'
        c=self.call('/api/compile',{'source':text,'source_name':'float.pine'})
        value=self.call('/api/launches/requirements',self.payload(c,inputs={'n':2.0},explain=True))
        self.assertIn('2.0',value['passport_json'])
        self.assertEqual(canonical(loads(value['passport_json'])),canonical(value['passport']))

    def test_rejected_source_cli_retains_version_reason_and_line(self):
        source=self.work/'unavailable.pine';source.write_text('//@version=3\nstudy("version")\nplot(time_close)\n',encoding='utf-8')
        process=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','passport','--source',str(source)],cwd=ROOT,capture_output=True,timeout=60)
        self.assertEqual(process.returncode,2,process.stderr)
        value=loads(process.stdout);self.assertEqual(value['issues'][0]['kind'],'version')
        self.assertEqual(value['diagnostics'][0]['code'],'ENVIRONMENT_VERSION');self.assertEqual(value['diagnostics'][0]['span']['line'],3)

    def test_explanation_flag_and_transport_fields_are_closed(self):
        c=self.compiled()
        for extra in ({'explain':1},{'explain':True,'path':'../x'},{'mode_options':{}}):
            with self.assertRaisesRegex(AssertionError,'400'):self.call('/api/launches/requirements',self.payload(c,**extra))
        self.assertEqual(self.server.jobs.list(),[])

    def test_bad_configuration_returns_blocked_report_not_false_ready(self):
        c=self.compiled();value=self.call('/api/launches/requirements',self.payload(c,explain=True,mode_options={'unexpected':True}))
        self.assertEqual(value['status'],'rejected');self.assertEqual(value['passport']['readiness']['status'],'blocked')
        self.assertEqual(self.server.jobs.list(),[])

    def test_saved_preparation_inspection_does_not_advance_or_submit(self):
        c=self.compiled();snapshot=self.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/pipeline/bars.json').read_text()})['sha256']
        config={'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':c['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail'}
        saved=self.call('/api/launches',config);self.assertEqual(saved['status'],'ready')
        with (patch.object(self.server.launches,'status',side_effect=AssertionError('inspection must not advance preparation')),
                patch.object(self.server.history,'submit',side_effect=AssertionError('no acquisition'))):
            report=self.call('/api/launches/'+config['request_id']+'/passport')['passport']
        self.assertEqual(report['readiness']['status'],'saved_plan_compatible')
        self.assertTrue(report['saved_preparation']['data_validation_recorded']);self.assertFalse(report['saved_preparation']['data_checked_now'])
        self.assertEqual(self.server.jobs.list(),[])
        self.assertEqual(self.server.launches.repository.get(config['request_id'])['state']['status'],'ready')

if __name__=='__main__':unittest.main()
