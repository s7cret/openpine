"""Token/host protected finite corpus routes with real saved reports."""
import json
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from apps.ui.server import Server
from packages.contracts.wire import digest


class CorpusHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=Server(('127.0.0.1',0),cls.temp.name)
        cls.thread=threading.Thread(target=cls.server.serve_forever);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(10);cls.server.server_close();cls.temp.cleanup()

    def call(self,path,payload=None,token=None):
        request=urllib.request.Request(self.base+path,data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type':'application/json','X-OpenPine-Token':self.server.token if token is None else token})
        try:response=urllib.request.urlopen(request,timeout=60)
        except urllib.error.HTTPError as error:response=error
        with response:return response.status,json.loads(response.read())

    def test_closed_fields_queries_and_token_protection(self):
        code,value=self.call('/api/corpus');self.assertEqual(code,200,value);self.assertEqual(len(value['cases']),24)
        self.assertEqual(self.call('/api/corpus?path=C:/secret')[0],400)
        payload={'request_id':'e'*32,'case_ids':['unsupported-v6']}
        self.assertEqual(self.call('/api/corpus/runs',payload,token='bad')[0],403)
        self.assertEqual(self.call('/api/corpus/runs',{**payload,'source':'x'})[0],400)
        self.assertEqual(self.call('/api/corpus/runs',{**payload,'case_ids':['../../secret']})[0],400)
        self.assertEqual(self.call('/api/corpus/runs/'+('0'*32))[0],404)

    def test_expected_rejection_report_and_explicit_single_case_rerun(self):
        payload={'request_id':'d'*32,'case_ids':['unsupported-v1','unsupported-v6']}
        self.assertEqual(self.call('/api/corpus/runs',payload)[0],202)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            code,value=self.call('/api/corpus/runs/'+payload['request_id'])
            self.assertEqual(code,200,value)
            if value['report']['status']!='running':break
            time.sleep(.1)
        self.assertEqual(value['report']['status'],'passed',value)
        self.assertEqual(value['sha256'],digest(value['report']))
        self.assertEqual(value['report']['summary']['counts']['passed'],0)
        self.assertEqual(value['report']['summary']['counts']['expected_rejection'],2)
        self.assertEqual(self.call('/api/corpus/runs',payload)[1]['run_id'],payload['request_id'])
        child={'request_id':'f'*32,'case_ids':['unsupported-v6'],'parent_id':payload['request_id']}
        self.assertEqual(self.call('/api/corpus/runs',child)[0],202)
        self.assertEqual(self.call('/api/corpus/runs/'+payload['request_id'])[1],value)
        self.assertIn(child['request_id'],[r['run_id'] for r in self.call('/api/corpus/runs')[1]['runs']])


if __name__=='__main__':unittest.main()
