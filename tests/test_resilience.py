"""Deterministic admission, transport retries, deadlines and submission persistence."""
import concurrent.futures,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from apps.openpine.http_client import LocalClient,LocalAPIError,Submission,client_rejection
from packages.execution.limits import WorkLimiter,CoordinatorBusy
from packages.contracts.wire import canonical,read_json,digest

BUSY={'status':'rejected','diagnostics':[{'code':'COORDINATOR_BUSY','message':'busy'}],'admission':{'retryable':True}}
def response(status,value):
    r=MagicMock();r.status=status;r.read.return_value=canonical(value);r.getheader.return_value=None;return r

class ResilienceTests(unittest.TestCase):
    def client(self):
        c=LocalClient('http://127.0.0.1:1234');c.token='token';return c
    def test_busy_get_retries_closes_connections_and_preserves_result(self):
        instances=[MagicMock(),MagicMock()]
        instances[0].getresponse.return_value=response(503,BUSY);instances[1].getresponse.return_value=response(200,{'ok':1})
        with patch('apps.openpine.http_client.HTTPConnection',side_effect=instances),patch('apps.openpine.http_client.time.sleep') as delay:
            self.assertEqual(self.client().call('/api/example'),{'ok':1});delay.assert_called_once_with(.05)
        for instance in instances:instance.close.assert_called_once()
    def test_storage_and_mixed_diagnostics_are_never_retried(self):
        for diagnostics in ([{'code':'STORAGE_UNAVAILABLE'}],[{'code':'COORDINATOR_BUSY'},{'code':'STORAGE_UNAVAILABLE'}]):
            value={'status':'rejected','diagnostics':diagnostics};instance=MagicMock();instance.getresponse.return_value=response(503,value)
            with patch('apps.openpine.http_client.HTTPConnection',return_value=instance) as factory,self.assertRaises(LocalAPIError) as caught:self.client().call('/api/example')
            self.assertEqual(client_rejection(caught.exception,'WRAPPED'),value);factory.assert_called_once()
    def test_unkeyed_post_is_never_replayed_even_when_busy(self):
        instance=MagicMock();instance.getresponse.return_value=response(503,BUSY)
        with patch('apps.openpine.http_client.HTTPConnection',return_value=instance) as factory,self.assertRaises(LocalAPIError):self.client().call('/api/searches',{'x':1})
        factory.assert_called_once()
    def test_keyed_post_reuses_identical_key_and_bytes_after_lost_response(self):
        instances=[MagicMock(),MagicMock()];instances[0].getresponse.side_effect=ConnectionResetError('lost');instances[1].getresponse.return_value=response(201,{'accepted':True})
        with patch('apps.openpine.http_client.HTTPConnection',side_effect=instances),patch('apps.openpine.http_client.time.sleep'):
            self.assertEqual(self.client().call('/api/searches',{'x':1},request_id='a'*32),{'accepted':True})
        self.assertEqual(instances[0].request.call_args,instances[1].request.call_args)
        self.assertEqual(instances[0].request.call_args.kwargs['headers']['X-OpenPine-Request'],'a'*32)
    def test_retry_count_and_deadline_are_bounded_and_keyboard_interrupt_cancels(self):
        instance=MagicMock();instance.getresponse.return_value=response(503,BUSY)
        with patch('apps.openpine.http_client.HTTPConnection',return_value=instance) as factory,patch('apps.openpine.http_client.time.sleep'),self.assertRaises(LocalAPIError):self.client().call('/api/example')
        self.assertEqual(factory.call_count,8)
        with patch('apps.openpine.http_client.HTTPConnection',return_value=instance) as factory,patch('apps.openpine.http_client.time.monotonic',side_effect=[0,0,121]),self.assertRaises(LocalAPIError):self.client().call('/api/example')
        factory.assert_called_once()
        with patch('apps.openpine.http_client.HTTPConnection',return_value=instance) as factory,patch('apps.openpine.http_client.time.sleep',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):self.client().call('/api/example')
        factory.assert_called_once()
    def test_retry_after_honored_with_cap_and_protocol_errors_not_hidden(self):
        instance=MagicMock();instance.getresponse.return_value=response(503,BUSY);instance.getresponse.return_value.getheader.return_value='100000'
        with patch('apps.openpine.http_client.HTTPConnection',return_value=instance),patch('apps.openpine.http_client.time.sleep') as delay,self.assertRaises(LocalAPIError):self.client().call('/api/example')
        self.assertTrue(all(c.args==(2.,) for c in delay.call_args_list))
        instance.getresponse.return_value.read.return_value=b'<html>bad</html>'
        with patch('apps.openpine.http_client.HTTPConnection',return_value=instance) as factory,self.assertRaises(LocalAPIError) as caught:self.client().call('/api/example')
        self.assertEqual(caught.exception.response['diagnostics'][0]['code'],'HTTP_PROTOCOL');factory.assert_called_once()
    def test_only_admitted_creation_routes_accept_request_keys(self):
        for path,body,key in (('/api/jobs',{},'a'*32),('/api/searches/abc/resume',{},'a'*32),('/api/searches',None,'a'*32),('/api/searches',{},'bad')):
            with self.assertRaises(ValueError):self.client().call(path,body,request_id=key)
    def test_metadata_remains_available_under_full_heavy_reservation(self):
        limiter=WorkLimiter();prepare=threading.Event();read=threading.Event();release=threading.Event()
        def hold(kind,event):
            with limiter.slot(kind):event.set();release.wait(5)
        threads=[threading.Thread(target=hold,args=(kind,event)) for kind,event in (('prepare',prepare),('read',read))]
        for t in threads:t.start()
        self.assertTrue(prepare.wait(5) and read.wait(5))
        try:
            self.assertEqual(limiter.reserved_mb,384)
            with limiter.slot('metadata'):
                self.assertEqual(limiter.reserved_mb,384)
                with self.assertRaises(CoordinatorBusy) as caught:
                    with limiter.slot('read'):pass
                self.assertEqual(caught.exception.admission['reason'],'reservation_upgrade')
            with self.assertRaises(CoordinatorBusy) as caught:
                with limiter.slot('read'):pass
            self.assertEqual(caught.exception.admission['reason'],'memory_reservation')
        finally:
            release.set()
            for t in threads:t.join(5)
        self.assertEqual(limiter.active,{'read':0,'prepare':0,'metadata':0})
    def test_busy_reasons_and_release_after_failure(self):
        limiter=WorkLimiter()
        with patch.object(limiter,'healthy',return_value=False),self.assertRaises(CoordinatorBusy) as caught:
            with limiter.slot('metadata'):pass
        self.assertEqual(caught.exception.admission['reason'],'resident_memory')
        with self.assertRaises(RuntimeError),limiter.slot('metadata'):raise RuntimeError('failed read')
        self.assertEqual(limiter.active['metadata'],0)
        with self.assertRaises(ValueError):
            with limiter.slot('unknown'):pass
    def args(self,path):return SimpleNamespace(operation_file=path,request=path.parent/'request.json',source=None,bars=None,output=path.parent/'out.json',request_id=None,new_request=False,server='http://127.0.0.1:1234')
    def test_submission_recovers_lost_response_and_reuses_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'operation.json';args=self.args(path);client=self.client();payload={'name':'one'}
            client.call=MagicMock(side_effect=[LocalAPIError(404,{}),LocalAPIError(0,{'diagnostics':[{'code':'RESPONSE_UNCERTAIN'}]})])
            first=Submission(args,'search',payload,client);key=first.value['request_id']
            with self.assertRaises(LocalAPIError):first.submit()
            self.assertEqual(read_json(path)['status'],'pending')
            receipt={'resource_id':'b'*32,'search_id':'b'*32,'request_sha256':digest(payload)};client.call=MagicMock(return_value=receipt)
            second=Submission(args,'search',payload,client);self.assertEqual(second.value['request_id'],key);self.assertEqual(second.submit(),receipt)
            self.assertEqual(read_json(path)['status'],'accepted');client.call.assert_called_once_with('/api/operations/search/'+key)
    def test_pending_client_file_rejects_changed_request_and_is_exclusively_owned(self):
        from packages.contracts import OpenPineError
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'operation.json';args=self.args(path);client=self.client();first=Submission(args,'search',{'x':1},client)
            with self.assertRaises(OpenPineError):Submission(args,'search',{'x':1},client)
            first.lease.close()
            with self.assertRaisesRegex(ValueError,'Recover'):Submission(args,'search',{'x':2},client)
            self.assertEqual(read_json(path)['payload'],{'x':1})
    def test_saved_intent_reuses_frozen_payload_even_when_new_refs_would_differ(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'operation.json';args=self.args(path);args.client_intent='same-input-files';client=self.client();client.call=MagicMock(side_effect=[LocalAPIError(404,{}),LocalAPIError(0,{})])
            first=Submission(args,'search',{'input_ref':'old-ref'},client);key=first.value['request_id']
            with self.assertRaises(LocalAPIError):first.submit()
            receipt={'resource_id':'b'*32,'search_id':'b'*32,'request_sha256':digest({'input_ref':'old-ref'})};client.call=MagicMock(return_value=receipt)
            second=Submission(args,'search',{'input_ref':'new-ref'},client);self.assertEqual(second.payload,{'input_ref':'old-ref'});self.assertEqual(second.value['request_id'],key);second.submit()
    def test_definitive_rejection_with_no_receipt_allows_corrected_settings(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'operation.json';args=self.args(path);client=self.client();client.call=MagicMock(side_effect=[LocalAPIError(404,{}),LocalAPIError(400,{'diagnostics':[{'code':'REQUEST_INVALID'}]}),LocalAPIError(404,{})])
            first=Submission(args,'search',{'x':1},client)
            with self.assertRaises(LocalAPIError):first.submit()
            self.assertEqual(read_json(path)['status'],'rejected')
            second=Submission(args,'search',{'x':2},client);self.assertEqual(second.payload,{'x':2});second.lease.close()

if __name__=='__main__':unittest.main()
