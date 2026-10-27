"""Cache isolation, content invalidation and bounded preparation/retry semantics."""
import concurrent.futures,shutil,tempfile,unittest,os,threading
from pathlib import Path
from unittest.mock import patch,MagicMock
from packages.compiler.semantic import snapshot_cache as cache,specification
from packages.execution.session import Session
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.transport import read_only_post
from packages.contracts.wire import read_json
from apps.openpine.bootstrap import local_manager
from apps.openpine.http_client import LocalClient,LocalAPIError
from test_prepared_requests_pipeline import prepare
from test_resilience import response,BUSY

class PreparationTests(unittest.TestCase):
    def setUp(self):cache.clear()
    def tearDown(self):cache.clear()
    def test_cached_snapshots_and_catalogues_cannot_share_mutable_state(self):
        a=Session(profile_id=HISTORICAL_PROFILE_ID);b=Session(profile_id=HISTORICAL_PROFILE_ID)
        self.assertEqual(cache.info()['validations'],1)
        a.snapshot.functions['versions']['6']['policy']['missing_bool_branch']='broken';a.catalog.contracts.clear()
        p,r=b.run('//@version=6\nindicator("Independent")\nplot(3)',data=__import__('test_functions').bars([1]))
        self.assertEqual(r.outputs[0].values,(3,));self.assertTrue(b.catalog.contracts)
        self.assertNotEqual(cache.load_snapshot().functions['versions']['6']['policy']['missing_bool_branch'],'broken')
    def test_parallel_cold_admission_validates_once_and_returns_isolated_templates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(lambda _:cache.load_snapshot(),range(3)))
        self.assertEqual(cache.info()['validations'],1);self.assertEqual(len({id(s.registry) for s in rows}),3)
    def test_same_size_tampering_with_preserved_mtime_is_not_hidden_by_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);shutil.copytree(specification.ROOT/'spec',root/'spec');cache.load_snapshot(root)
            lock=read_json(root/'spec/lock.json');path=root/lock['snapshot']/'manifest.json';stat=path.stat();raw=path.read_bytes()
            changed=raw.replace(b'"schema_version"',b'"schema_versioX"',1);self.assertNotEqual(raw,changed)
            path.write_bytes(changed);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            with self.assertRaises(specification.SpecError) as caught:cache.load_snapshot(root)
            self.assertEqual(caught.exception.code,'SPEC_HASH')
    def test_failed_validation_never_populates_cache(self):
        with patch.object(specification,'load_snapshot',side_effect=specification.SpecError('SPEC_HASH','corrupt')):
            with self.assertRaises(specification.SpecError):cache.load_snapshot()
        self.assertEqual(cache.info()['entries'],0);self.assertEqual(cache.info()['validations'],0)
    def test_distinct_roots_evict_old_templates_instead_of_growing_without_limit(self):
        with tempfile.TemporaryDirectory() as d:
            roots=[]
            for i in range(3):
                root=Path(d)/str(i);shutil.copytree(specification.ROOT/'spec',root/'spec');roots.append(root);cache.load_snapshot(root)
            self.assertEqual(cache.info()['entries'],cache.info()['limit']);before=cache.info()['validations']
            cache.load_snapshot(roots[0]);self.assertEqual(cache.info()['validations'],before+1)
    def test_batch_reuses_ir_but_rechecks_immutable_bytes_and_discards_on_failure(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as m:
            raw,_=prepare(m);content=m.objects.get(raw['input_ref']);ref=content['artifact_ref'];path=m.objects.path(ref);original=path.read_bytes()
            with m.preparation.batch():
                first=m.preparation.program(content);self.assertIs(first,m.preparation.program(content))
                path.write_bytes(original+b' ')
                try:
                    with self.assertRaisesRegex(ValueError,'hash mismatch'):m.preparation.program(content)
                finally:path.write_bytes(original)
            self.assertIsNot(first,m.preparation.program(content));self.assertIsNone(m.preparation._local.programs)
    def test_batch_failure_and_code_change_cannot_leave_a_shared_or_publishable_plan(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as m:
            with patch('packages.execution.preparation.plan_identity',side_effect=RuntimeError('unavailable')):
                with self.assertRaises(RuntimeError),m.preparation.batch():pass
            self.assertIsNone(m.preparation._local.programs)
            with patch('packages.execution.preparation.plan_identity',side_effect=[('a','b'),('changed','b')]):
                with self.assertRaisesRegex(Exception,'changed during preparation'),m.preparation.batch():pass
            self.assertIsNone(m.preparation._local.programs);self.assertEqual(m.list(),[])
    def test_read_only_post_routes_retry_without_replaying_creation_or_controls(self):
        client=LocalClient('http://127.0.0.1:1234');client.token='t'
        for path in ('/api/launches/requirements','/api/launches/history-spec','/api/launches/'+'a'*32+'/research-base'):
            a,b=MagicMock(),MagicMock();a.getresponse.return_value=response(503,BUSY);b.getresponse.return_value=response(200,{'ok':True})
            with patch('apps.openpine.http_client.HTTPConnection',side_effect=[a,b]),patch('apps.openpine.http_client.time.sleep'):
                self.assertEqual(client.call(path,{}),{'ok':True})
            self.assertEqual(a.request.call_args,b.request.call_args)
        for path in ('/api/compile','/api/launches','/api/launches/'+'a'*32+'/start','/api/launches/'+'a'*32+'/research-base?x=1'):
            self.assertFalse(read_only_post(path));a=MagicMock();a.getresponse.return_value=response(503,BUSY)
            with patch('apps.openpine.http_client.HTTPConnection',return_value=a) as factory,self.assertRaises(LocalAPIError):client.call(path,{})
            factory.assert_called_once()
    def test_read_only_post_respects_non_retryable_admission_and_mixed_errors(self):
        client=LocalClient('http://localhost:1234');client.token='t'
        for payload in ({**BUSY,'admission':{'retryable':False}}, {'diagnostics':[{'code':'COORDINATOR_BUSY'},{'code':'STORAGE_UNAVAILABLE'}]}):
            a=MagicMock();a.getresponse.return_value=response(503,payload)
            with patch('apps.openpine.http_client.HTTPConnection',return_value=a) as factory,self.assertRaises(LocalAPIError):client.call('/api/launches/requirements',{})
            factory.assert_called_once()

if __name__=='__main__':unittest.main()
