"""Transport carries checked bindings and reports recorded replay prefixes."""
import json,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict
from apps.ui.server import Server
from packages.compiler.semantic.specification import ROOT
from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical,loads,digest
from packages.execution.host_memory import HostAdmission
from tools.acceptance_http import request_json

class BoundPassportHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=Server(('127.0.0.1',0),Path(cls.temp.name)/'http')
        cls.server.jobs.limiter.host=HostAdmission(probe=lambda:{'total_bytes':16*1024**3,'available_bytes':8*1024**3,'source':'test.fixed'})
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(10);cls.server.server_close();cls.temp.cleanup()
    def call(self,path,payload=None):return request_json(self.base,self.server.token,path,payload)
    def compile(self,v=6):return self.call('/api/compile',{'source':(ROOT/f'examples/pipeline/v{v}.pine').read_text(),'source_name':f'owned-v{v}.pine'})
    def test_all_versions_http_binding_check_and_original_json(self):
        snapshot=self.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/pipeline/bars.json').read_text()})['sha256']
        for v in range(1,7):
            compiled=self.compile(v);key=uuid.uuid4().hex
            self.call('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':key,'input_ref':compiled['input_ref'],
                'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'gap_policy':'fail','timeout_seconds':120})
            saved=self.call(f'/api/launches/{key}/passport');binding=saved['passport']['binding']['sha256']
            with patch.object(self.server.history,'submit',side_effect=AssertionError('no acquisition')):
                value=self.call(f'/api/launches/{key}/validate',{'binding_sha256':binding})
            self.assertEqual(value['validation']['status'],'validated',value['validation'])
            self.assertEqual(canonical(value['passport']),canonical(loads(value['passport_json'])))
            self.assertEqual(self.call(f'/api/launches/{key}/execution')['execution']['status'],'not_submitted')
            with self.assertRaisesRegex(AssertionError,'400'):self.call(f'/api/launches/{key}/start',{'binding_sha256':'0'*64})
        self.assertEqual(self.server.jobs.repository.list(),[])

    def test_closed_protocol_fields(self):
        for path in ('/api/launches/'+32*'a'+'/validate','/api/replays/'+32*'a'+'/validate'):
            for payload in ({'path':'../x'},{'binding_sha256':1},{'binding_sha256':'x'}):
                with self.assertRaisesRegex(AssertionError,'400'):self.call(path,payload)

    def test_replay_tape_verification_does_not_advance_and_rejects_stale_chain(self):
        compiled=self.compile();context=asdict(MarketContext('primary','TEST:PIPELINE','1','uploaded-file.1'))
        replay=self.call('/api/replays',{'input_ref':compiled['input_ref'],'contexts':[context],'primary_id':'primary','broker_settings':None,'timeout_seconds':120})
        key=replay['replay_id'];saved=self.call(f'/api/replays/{key}/passport')['passport']
        from packages.contracts.realtime import MarketEvent
        event=MarketEvent(0,'primary','close',0,60000,0,10.,10.,10.,10.,0.).payload()
        self.call(f'/api/replays/{key}/events',{'events':[event]})
        blocked=self.call(f'/api/replays/{key}/validate',{'binding_sha256':saved['replay_binding']['sha256']})['passport']
        self.assertEqual(blocked['replay_validation']['status'],'blocked')
        before=self.server.replays.repository.row(key)
        with patch.object(self.server.replays,'advance',side_effect=AssertionError('inspection must not execute')):
            report=self.call(f'/api/replays/{key}/validate',{})['passport']
        self.assertEqual(report['replay_validation']['status'],'tape_verified',report)
        self.assertEqual(report['replay_validation']['events_verified'],1)
        self.assertEqual(before,self.server.replays.repository.row(key))
        self.assertFalse(report['evidence']['whole_program_execution_verified'])
        self.server.jobs.db.execute('UPDATE replay_events SET event_sha=? WHERE replay_id=? AND sequence=0',('0'*64,key))
        report=self.call(f'/api/replays/{key}/validate',{})['passport']
        self.assertEqual(report['replay_validation']['status'],'blocked')

if __name__=='__main__':unittest.main()
