"""All language versions use the same configured CLI/HTTP/worker contract."""
import json
from pathlib import Path
import tempfile
import time
import unittest
import test_http as fixture
from apps.openpine.cli import main
from packages.compiler.semantic.specification import ROOT


class InputHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.HTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.HTTPTests.tearDownClass.__func__)
    call=classmethod(fixture.HTTPTests.call.__func__)
    finish=fixture.HTTPTests.finish

    def test_saved_artifact_download_preserves_exact_int64_and_program_checksum(self):
        from packages.execution.artifacts import import_program
        source='//@version=6\nindicator("exact export")\nn=input.int(9007199254740993)\nplot(n)'
        compiled=self.call('/api/compile',{'source':source,'source_name':'int64.pine'})[1]
        status,artifact=self.call('/api/artifacts/'+compiled['artifact_ref'])
        self.assertEqual(status,200);self.assertEqual(artifact,compiled['artifact'])
        program=import_program(artifact)
        from packages.contracts.inputs import definitions
        self.assertEqual(definitions(program)[0].default,9007199254740993)
        self.assertEqual(self.call('/api/artifacts/'+'0'*64)[0],400)

    def test_cli_rejects_malformed_override_file_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);overrides=root/'inputs.json';output=root/'result.json'
            overrides.write_text('{"length":2,"length":3}',encoding='utf-8')
            self.assertEqual(main(['run','--source',str(ROOT/'examples/inputs/v6.pine'),'--bars',str(ROOT/'examples/inputs/bars.json'),'--inputs',str(overrides),'--output',str(output)]),2)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['status'],'rejected')

    def test_all_versions_cli_http_overrides_and_validation(self):
        example=ROOT/'examples/inputs'
        for version in range(1,7):
            source=example/f'v{version}.pine'
            status,compiled=self.call('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.as_posix()})
            self.assertEqual(status,200,compiled);self.assertEqual(len(compiled['inputs']['definitions']),5)
            request={'schema_version':'openpine.job-request.6','input_ref':compiled['input_ref'],'snapshot_id':self.snapshot['sha256'],
                'timeout_seconds':60,'inputs':{'length':4,'scale':2,'src':'open'}}
            status,job=self.call('/api/jobs',request);self.assertEqual(status,202,job)
            self.assertEqual(self.finish(job['job_id'])['status'],'succeeded')
            result=self.call(f"/api/jobs/{job['job_id']}/result")[1]['result']
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);bars=root/'bars.json';bars.write_text(json.dumps(self.snapshot['data']),encoding='utf-8');output=root/'result.json'
                code=main(['run','--source',str(source),'--bars',str(bars),'--inputs',str(example/'overrides.json'),'--output',str(output)])
                self.assertEqual(code,0,output.read_text(encoding='utf-8'))
                self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['result'],result)
            self.assertEqual(self.call('/api/jobs',{**request,'inputs':{'length':0}})[0],400)

    def test_experiment_routes_presets_and_bounded_pages(self):
        source='//@version=6\nindicator("series")\ngain=input.int(2,minval=1,maxval=4)\nplot(close*gain)'
        compiled=self.call('/api/compile',{'source':source,'source_name':'series.pine'})[1]
        preset={'input_ref':compiled['input_ref'],'name':'sample','inputs':{'gain':3},'expected_revision':None}
        self.assertEqual(self.call('/api/presets',preset)[0],201)
        self.assertEqual(self.call('/api/presets',preset)[0],400)
        self.assertEqual(len(self.call('/api/presets?input_ref='+compiled['input_ref'])[1]['presets']),1)
        status,group=self.call('/api/experiments',{'name':'HTTP grid','input_ref':compiled['input_ref'],'snapshot_id':self.snapshot['sha256'],'timeout_seconds':60,'grid':{'gain':[2,3]}})
        self.assertEqual(status,201,group);identifier=group['experiment_id'];deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            group=self.call('/api/experiments/'+identifier)[1]
            if group['status']!='running':break
            time.sleep(.04)
        self.assertEqual(group['status'],'succeeded',group)
        self.assertEqual([v['inputs']['input:gain'] for v in group['variants']],[2,3])
        self.assertEqual(self.call('/api/experiments/'+identifier+'?start=1&limit=1')[1]['variants'],group['variants'][1:])
        self.assertEqual(self.call('/api/experiments/'+identifier+'?limit=101')[0],400)
        self.assertEqual(self.call('/api/experiments/'+identifier+'/resume',{'inputs':{}})[0],400)
        self.assertTrue(self.call('/parameters.js')[1]);self.assertTrue(self.call('/experiments.js')[1])

    def test_enum_inputs_roundtrip_artifacts_presets_workers_and_cli(self):
        from packages.execution.artifacts import import_program
        from packages.contracts.inputs import definitions
        for version in (5,6):
            source=f'//@version={version}\nindicator("Enum HTTP")\nenum Mode\n    buy="Buy"\n    sell="Sell"\nmode=input.enum(Mode.buy,"Mode")\nplot(mode==Mode.buy ? 1 : 2)\n'
            status,compiled=self.call('/api/compile',{'source':source,'source_name':'enum-http.pine'})
            self.assertEqual(status,200,compiled);definition=compiled['inputs']['definitions'][0];selected=definition['options'][1]
            self.assertEqual(definition['options_text'],['Buy','Sell'])
            artifact=self.call('/api/artifacts/'+compiled['artifact_ref'])[1];program=import_program(artifact)
            self.assertEqual(definitions(program)[0].options[1],selected)
            preset={'input_ref':compiled['input_ref'],'name':'enum-'+str(version),'inputs':{'mode':selected},'expected_revision':None}
            self.assertEqual(self.call('/api/presets',preset)[0],201)
            presets=self.call('/api/presets?input_ref='+compiled['input_ref'])[1]['presets'];self.assertEqual(presets[0]['inputs']['input:mode'],selected)
            request={'schema_version':'openpine.job-request.6','input_ref':compiled['input_ref'],'snapshot_id':self.snapshot['sha256'],'timeout_seconds':60,'inputs':{'mode':selected}}
            status,job=self.call('/api/jobs',request);self.assertEqual(status,202,job);self.assertEqual(self.finish(job['job_id'])['status'],'succeeded')
            result=self.call(f"/api/jobs/{job['job_id']}/result")[1]['result']
            self.assertTrue(all(v==2 for v in result['outputs'][0]['values']))
            status,group=self.call('/api/experiments',{'name':'Enum grid '+str(version),'input_ref':compiled['input_ref'],'snapshot_id':self.snapshot['sha256'],'timeout_seconds':60,'grid':{'mode':list(definition['options'])}})
            self.assertEqual(status,201,group);labels=self.call('/api/experiments/'+group['experiment_id'])[1]['input_labels']
            self.assertEqual(labels['input:mode'][selected],'Sell')
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                state=self.call('/api/experiments/'+group['experiment_id'])[1]
                if state['status']!='running':break
                time.sleep(.04)
            self.assertEqual(state['status'],'succeeded',state)
            for bad in ('Sell',1,'enum:99:1:2:1'):
                self.assertEqual(self.call('/api/jobs',{**request,'inputs':{'mode':bad}})[0],400)
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);saved_ir=root/'enum-ir.json';bars=root/'bars.json';inputs=root/'inputs.json';out=root/'result.json'
                saved_ir.write_text(json.dumps(artifact),encoding='utf-8');bars.write_text(json.dumps(self.snapshot['data']),encoding='utf-8');inputs.write_text(json.dumps({'mode':selected}),encoding='utf-8')
                self.assertEqual(main(['run','--artifact',str(saved_ir),'--bars',str(bars),'--inputs',str(inputs),'--output',str(out)]),0)
                self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['result'],result)


if __name__=='__main__':unittest.main()
