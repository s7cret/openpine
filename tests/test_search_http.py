"""CLI/HTTP share search contracts, frozen reruns, controls and bounded pages."""
import json
from pathlib import Path
import tempfile
import time
import unittest
import test_http as fixture
from apps.openpine.cli import main
from packages.compiler.semantic.specification import ROOT


class SearchHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.HTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.HTTPTests.tearDownClass.__func__)
    call=classmethod(fixture.HTTPTests.call.__func__)
    finish=fixture.HTTPTests.finish

    def complete(self,identifier):
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            code,state=self.call('/api/searches/'+identifier)
            self.assertEqual(code,200,state)
            if state['status'] not in ('selecting','checking_holdout'):return state
            time.sleep(.05)
        self.fail(state)

    def test_cli_http_v1_v6_and_repeat_frozen_candidate(self):
        example=ROOT/'examples/search';request=json.loads((example/'request.json').read_text(encoding='utf-8'))
        snapshot=self.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/inputs/bars.json').read_text(encoding='utf-8')})[1]['sha256']
        for version in (1,6):
            source=example/f'v{version}.pine';compiled=self.call('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.name})[1]
            code,created=self.call('/api/searches',{**request,'input_ref':compiled['input_ref'],'snapshot_id':snapshot})
            self.assertEqual(code,201,created);identifier=created['search_id'];state=self.complete(identifier)
            self.assertEqual(state['status'],'succeeded',state);self.assertEqual(state['decision']['winner_ordinal'],2)
            self.assertEqual([r['assessment']['score'] for r in state['variants']],['12.0','24.0','36.0'])
            self.assertEqual(state['holdout']['assessment']['score'],'43.5')
            self.assertEqual(self.call('/api/searches/'+identifier+'?start=1&limit=1')[1]['variants'],state['variants'][1:2])
            self.assertEqual(self.call('/api/searches/'+identifier+'?limit=101')[0],400)
            self.assertEqual(self.call('/api/searches/'+identifier+'/resume',{'inputs':{}})[0],400)
            if version==6:
                repeat=self.call('/api/searches/'+identifier+'/rerun',{'ordinal':2,'phase':'holdout'})
                self.assertEqual(repeat[0],201,repeat);group=repeat[1]['experiment_id'];deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    rows=self.call('/api/experiments/'+group)[1]['variants']
                    if rows[0]['job_id']:break
                    time.sleep(.05)
                self.assertEqual(self.finish(rows[0]['job_id'])['status'],'succeeded')
                repeat_result=self.call('/api/jobs/'+rows[0]['job_id']+'/result')[1]['result']
                original=self.call('/api/jobs/'+state['holdout']['job_id']+'/result')[1]['result']
                self.assertEqual(repeat_result,original);self.assertIn('evaluation_window',original)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'result.json'
            self.assertEqual(main(['search','run','--request',str(example/'request.json'),'--source',str(example/'v6.pine'),'--bars',str(ROOT/'examples/inputs/bars.json'),'--state-dir',str(Path(directory)/'jobs'),'--output',str(output)]),0)
            state=json.loads(output.read_text(encoding='utf-8'));self.assertEqual(state['status'],'succeeded');self.assertEqual(state['holdout']['assessment']['score'],'43.5')

    def test_auth_validation_cancel_and_served_interface(self):
        example=ROOT/'examples/search';request=json.loads((example/'request.json').read_text(encoding='utf-8'))
        self.assertEqual(self.call('/api/searches',{},headers={'X-OpenPine-Token':'wrong'})[0],403)
        self.assertEqual(self.call('/api/searches',{})[0],400)
        self.assertEqual(self.call('/api/searches?limit=5')[0],400)
        self.assertEqual(self.call('/api/searches/'+'0'*32)[0],400)
        compiled=self.call('/api/compile',{'source':(example/'v6.pine').read_text(encoding='utf-8'),'source_name':'cancel.pine'})[1]
        snapshot=self.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/inputs/bars.json').read_text(encoding='utf-8')})[1]['sha256']
        state=self.call('/api/searches',{**request,'input_ref':compiled['input_ref'],'snapshot_id':snapshot})[1];identifier=state['search_id']
        self.assertEqual(self.call('/api/searches/'+identifier+'/pause',{})[1]['status'],'paused')
        self.assertEqual(self.call('/api/searches/'+identifier+'/cancel',{})[1]['status'],'cancelled')
        self.assertEqual(self.call('/api/searches/'+identifier+'/resume',{})[0],400)
        self.assertEqual(self.call('/api/experiments/'+state['selection_id']+'/resume',{})[0],400)
        self.assertEqual(self.call('/optimization.js')[0],200)
        self.assertIn(b'/optimization.js',self.call('/')[1])

    def test_fractional_mean_survives_worker_storage_http_and_ranking(self):
        source='//@version=6\nindicator("Fractional mean")\nn=input.int(1)\nplot(bar_index==2 ? n : 0)'
        compiled=self.call('/api/compile',{'source':source,'source_name':'fraction.pine'})[1]
        snapshot=self.call('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/inputs/bars.json').read_text(encoding='utf-8')})[1]['sha256']
        request=json.loads((ROOT/'examples/search/request.json').read_text(encoding='utf-8'))
        request['windows']['selection'].update(score_start_ms=0,end_ms=180000)
        status,created=self.call('/api/searches',{**request,'input_ref':compiled['input_ref'],'snapshot_id':snapshot})
        self.assertEqual(status,201,created);state=self.complete(created['search_id'])
        self.assertEqual(state['status'],'succeeded',state)
        first=state['variants'][0]['assessment'];self.assertEqual(first['status'],'eligible')
        self.assertEqual(first['score'],'0.'+'3'*700)
        self.assertEqual(state['decision']['winner_ordinal'],2)
        self.assertEqual([r['ordinal'] for r in state['ranking']],[2,1,0])
        self.assertEqual(self.call('/api/searches/'+created['search_id'])[1]['variants'][0]['assessment'],first)


if __name__=='__main__':unittest.main()
