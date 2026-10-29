"""The public CLI and browser API share pins, controls and deletion review fences."""
import unittest,uuid,time,sys,subprocess,json
from apps.openpine.http_client import LocalAPIError
import test_trace_http as helpers

class ComparisonHTTPTests(unittest.TestCase):
    setUp=helpers.TraceHTTPTests.setUp
    close=helpers.TraceHTTPTests.close
    prepare=helpers.TraceHTTPTests.prepare
    def job(self):
        key=self.prepare();self.client.call(f'/api/launches/{key}/start',{})
        from test_jobs import wait
        self.assertEqual(wait(self.server.jobs,key)['status'],'succeeded');return key
    def test_cli_http_saved_report_chart_and_delete_review(self):
        from packages.compiler.semantic.specification import ROOT
        a=self.job();key=uuid.uuid4().hex
        bootstrap="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
        result=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),'compare','run','--server',self.url,
            '--left','job:'+a,'--right','job:'+a,'--id',key,'--wait'],cwd=self.temp.name,capture_output=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        value=json.loads(result.stdout);self.assertEqual(value,self.client.call('/api/comparisons/'+key));self.assertEqual(value['state']['status'],'equal')
        chart=self.client.call(f'/api/comparisons/{key}/chart?side=0&start=1&limit=1');self.assertTrue(chart['pinned']);self.assertEqual(len(chart['rows']),3);self.assertEqual(chart['rows'][0]['bar'],1)
        self.assertEqual(self.client.call('/api/comparisons?limit=1')['items'][0]['id'],key)
        review=self.client.call(f'/api/diagnostics/comparison/{key}/delete-preview',{})
        with self.assertRaises(LocalAPIError):self.client.call(f'/api/diagnostics/comparison/{key}/delete-apply',{'preview_id':'0'*64})
        self.assertTrue(self.client.call(f'/api/diagnostics/comparison/{key}/delete-apply',{'preview_id':review['preview_id']})['deleted'])
        self.assertEqual(self.server.jobs.get(a)['status'],'succeeded')
    def test_closed_routes_and_retention_no_implicit_work(self):
        before=len(self.server.jobs.repository.list())
        for path in ('/api/comparisons?limit=101','/api/comparisons?start=-1','/api/comparisons?limit=1&limit=2','/api/comparisons?other=1'):
            with self.assertRaises(LocalAPIError):self.client.call(path)
        with self.assertRaises(LocalAPIError):self.client.call('/api/comparisons',{'request_id':uuid.uuid4().hex,'extra':1})
        self.assertEqual(self.client.call('/api/diagnostics/retention'),{'keep_days':None})
        for payload in ({'keep_days':True},{'keep_days':0},{'keep_days':30,'auto_delete':True}):
            with self.assertRaises(LocalAPIError):self.client.call('/api/diagnostics/retention',payload)
        self.assertEqual(self.client.call('/api/diagnostics/retention',{'keep_days':30}),{'keep_days':30})
        self.assertEqual(self.client.call('/api/traces')['items'],[]);self.assertEqual(len(self.server.jobs.repository.list()),before)

if __name__=='__main__':unittest.main()
