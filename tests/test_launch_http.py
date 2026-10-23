"""Transport, real isolated workers, shared research data and acquisition preparation."""
import json
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
import uuid
from pathlib import Path

from apps.ui.server import Server
from packages.compiler.semantic.specification import ROOT
from packages.marketdata.history import BinanceHistory


class LaunchHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.server=Server(('127.0.0.1',0),cls.temp.name)
        cls.thread=threading.Thread(target=cls.server.serve_forever);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port);cls.token=cls.call('/api/config')[1]['token']
        cls.primary=cls.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/composite/bars.json').read_text()})[1]['sha256']
        cls.higher=cls.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/composite/higher.json').read_text()})[1]['sha256']

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(10);cls.server.server_close();cls.temp.cleanup()

    @classmethod
    def call(cls,path,payload=None):
        request=urllib.request.Request(cls.base+path,data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type':'application/json','X-OpenPine-Token':getattr(cls,'token','')})
        for attempt in range(12):
            try:response=urllib.request.urlopen(request,timeout=90)
            except urllib.error.HTTPError as error:response=error
            with response:code=response.status;value=json.loads(response.read())
            if code==503 and attempt<11:time.sleep(.1);continue
            return code,value

    def wait(self,path,target):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            code,state=self.call(path);self.assertEqual(code,200,state)
            if state['status']==target:return state
            if state['status'] in ('failed','cancelled','timed_out'):self.fail(state)
            time.sleep(.03)
        self.fail(state)

    def config(self,version,family):
        name=f'{family}-v{version}.pine';code,c=self.call('/api/compile',{'source':(ROOT/'examples/composite'/name).read_text(),'source_name':name})
        self.assertEqual(c['status'],'compiled',c)
        return {'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':c['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':self.primary},'contexts':[{'kind':'snapshot','snapshot_id':self.higher}],
            'inputs':{},'timeout_seconds':120,'gap_policy':'fail','checkpoint_interval_bars':17,'pause_after_bars':17}

    def test_v1_v6_both_families_prepare_resume_inspect_and_repeat_once(self):
        for version in (1,6):
            for family in ('macd-cross','bb-reversion'):
                config=self.config(version,family);code,ready=self.call('/api/launches',config)
                self.assertEqual((code,ready['status']),(201,'ready'),ready)
                key=ready['launch_id'];_,job=self.call('/api/launches/'+key+'/start',{})
                self.assertEqual(self.call('/api/launches/'+key+'/start',{})[1]['job_id'],job['job_id'])
                self.wait('/api/jobs/'+key,'paused');self.call('/api/jobs/'+key+'/resume',{});state=self.wait('/api/jobs/'+key,'succeeded')
                _,saved=self.call('/api/jobs/'+key+'/configuration')
                self.assertEqual(saved['source']['source_name'],f'{family}-v{version}.pine')
                self.assertEqual(saved['request'],ready['prepared']['request'])
                self.assertEqual(saved['plan']['contexts'],ready['base']['contexts'])
                self.assertEqual(state['resumed_from_bar'],17)
                _,chart=self.call('/api/jobs/'+key+'/chart?start=35&limit=40');self.assertEqual(len(chart['bars']),40)
                _,ledger=self.call('/api/jobs/'+key+'/ledger?start=0&limit=100');self.assertTrue(ledger)

    def test_saved_sources_snapshot_catalog_and_research_base_have_exact_contexts(self):
        config=self.config(6,'macd-cross');_,ready=self.call('/api/launches',config)
        key=ready['launch_id'];self.assertEqual(self.call('/api/launches/'+key+'/source')[1]['source']['source_name'],'macd-cross-v6.pine')
        self.assertEqual(self.call('/api/launches/'+key+'/sources')[1]['sources'],[{'symbol':'TEST:TECHNICAL','timeframe':'1'},{'symbol':'TEST:TECHNICAL','timeframe':'4'}])
        _,page=self.call('/api/launches/snapshots?limit=1&offset=0');self.assertEqual(len(page['items']),1);self.assertIsNotNone(page['next_offset'])
        _,requirements=self.call('/api/launches/requirements?input_ref='+config['input_ref']);self.assertEqual(requirements['contexts'][0]['symbol'],'TEST:TECHNICAL')
        base=ready['base'];code,experiment=self.call('/api/experiments',{'name':'Prepared contexts','grid':{'signalLength':[2,3]},**base})
        self.assertEqual(code,201,experiment)
        # The existing research service admits every frozen variant before dispatch.
        variants=self.server.experiments.repository.variants(experiment['experiment_id'])
        self.assertEqual(len(variants),2)
        for variant in variants:
            self.assertEqual(variant['request']['contexts'],base['contexts'])
            self.assertEqual(variant['plan'].payload()['contexts'],base['contexts'])
        self.call('/api/experiments/'+experiment['experiment_id']+'/cancel',{})
        bad={**config,'request_id':uuid.uuid4().hex,'contexts':[]};self.assertEqual(self.call('/api/launches',bad)[0],400)

    def test_existing_provider_acquires_every_context_and_restarts_preparation(self):
        from test_marketdata import instrument_payload,row
        class Exchange:
            def __call__(self,path,params,*,control=None):
                if control:control.check()
                if path.endswith('/time'):return {'serverTime':1800000}
                if path.endswith('/exchangeInfo'):return instrument_payload()
                size=300000 if params.get('interval')=='5m' else 60000
                values=[]
                for at in range(0,1200000,size):
                    if params['startTime']<=at<=params['endTime']:
                        value=row(at);value[6]=at+size-1;values.append(value)
                return values[:params['limit']]
        with tempfile.TemporaryDirectory() as directory:
            from apps.openpine.bootstrap import local_manager
            from apps.openpine.history_service import HistoryJobs
            from apps.openpine.launches import Launches
            from packages.contracts.market import InstrumentKey,HistoryRequest
            source='//@version=6\nindicator("provider contexts")\nlength=input.int(2)\nx=request.security("BINANCE:BTCUSDT","5",ta.ema(close,length))\nplot(x)'
            with local_manager(directory) as manager:
                history=HistoryJobs(manager,provider=BinanceHistory(Exchange()),start=False);service=Launches(manager,history)
                config={'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':manager.freeze_input({'source':source,'source_name':'provider.pine'}),
                    'primary':{'kind':'history','request':HistoryRequest(InstrumentKey.from_pine('BINANCE:BTCUSDT'),'1',0,1200000).payload()},
                    'contexts':[{'kind':'history','request':HistoryRequest(InstrumentKey.from_pine('BINANCE:BTCUSDT'),'5',0,1200000).payload()}],
                    'inputs':{},'timeout_seconds':120,'gap_policy':'fail'}
                pending=service.create(config);self.assertEqual(pending['status'],'preparing');history.close()
            with local_manager(directory) as manager:
                with HistoryJobs(manager,provider=BinanceHistory(Exchange())) as history:
                    service=Launches(manager,history);deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        state=service.status(config['request_id'])
                        if state['status']!='preparing':break
                        time.sleep(.03)
                    self.assertEqual(state['status'],'ready',state)
                    self.assertEqual([r['identity']['count'] for r in state['prepared']['datasets']],[20,4])
                    self.assertEqual(state['sources'],[{**p,'snapshot_id':s['snapshot_id']} for p,s in zip(pending['sources'],state['sources'])])


if __name__=='__main__':unittest.main()
