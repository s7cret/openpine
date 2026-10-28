"""Real loopback routes, token checks and an idempotent replacement worker."""
import json
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

from apps.ui.server import Server
from test_jobs import request


class UpgradeHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=Server(('127.0.0.1',0),cls.temp.name)
        cls.thread=threading.Thread(target=cls.server.serve_forever);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port);cls.token=cls.server.token

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(10);cls.server.server_close();cls.temp.cleanup()

    def call(self,path,payload=None,*,token=None):
        request=urllib.request.Request(self.base+path,data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type':'application/json','X-OpenPine-Token':self.token if token is None else token})
        try:response=urllib.request.urlopen(request,timeout=90)
        except urllib.error.HTTPError as error:response=error
        with response:return response.status,json.loads(response.read())

    def wait(self,identifier):
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            code,row=self.call('/api/jobs/'+identifier);self.assertEqual(code,200,row)
            if row['status'] in ('succeeded','failed','cancelled','timed_out'):return row
            time.sleep(.05)
        self.fail(row)

    def test_preview_apply_duplicate_and_real_worker(self):
        payload=request();snapshot=self.call('/api/snapshots',{'provider':'upload','raw':json.dumps(payload['snapshot'])})
        self.assertEqual(snapshot[0],201,snapshot)
        code,state=self.call('/api/jobs',{'schema_version':'openpine.job-submission.1','input':payload['input'],
            'snapshot_sha256':snapshot[1]['sha256'],'timeout_seconds':30})
        self.assertEqual(code,202,state);identifier=state['job_id'];self.assertEqual(self.wait(identifier)['status'],'succeeded')
        code,row=self.call('/api/jobs/'+identifier+'/diagnosis');self.assertEqual(code,200,row);self.assertEqual(row['action'],'view_result')
        code,report=self.call('/api/jobs/'+identifier+'/restart/preview',{});self.assertEqual(code,200,report)
        applied={'preview_id':report['preview_id'],'request_id':'c'*32}
        code,receipt=self.call('/api/jobs/'+identifier+'/restart/apply',applied);self.assertEqual(code,202,receipt)
        self.assertEqual(self.call('/api/jobs/'+identifier+'/restart/apply',applied)[1],receipt)
        self.assertEqual(self.wait(receipt['job_id'])['status'],'succeeded')
        value=self.call('/api/jobs/'+receipt['job_id']+'/result')[1]
        self.assertEqual(value['result']['outputs'][0]['values'][-1],135.)
        self.assertFalse(receipt['reuses_checkpoint'])

    def test_diagnostic_routes_are_bounded_and_request_fields_are_closed(self):
        code,report=self.call('/api/maintenance/doctor');self.assertEqual(code,200,report)
        self.assertEqual(report['status'],'ok');self.assertEqual(report['ownership'],'busy')
        code,value=self.call('/api/maintenance/compatibility?kind=jobs&limit=1');self.assertEqual(code,200,value)
        self.assertLessEqual(len(value['items']),1)
        self.assertEqual(self.call('/api/maintenance/compatibility?limit=101')[0],400)
        self.assertEqual(self.call('/api/maintenance/doctor?full=true')[0],400)
        code,package=self.call('/api/maintenance/diagnostics');self.assertEqual(code,200,package)
        self.assertNotIn(self.temp.name,json.dumps(package))
        self.assertEqual(self.call('/api/jobs/'+('0'*32)+'/restart/preview',{},token='bad')[0],403)
        self.assertEqual(self.call('/api/jobs/'+('0'*32)+'/restart/apply',{'unexpected':1})[0],400)


if __name__=='__main__':unittest.main()
