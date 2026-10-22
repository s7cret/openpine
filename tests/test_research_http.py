"""Server-owned CLI, reports, fold controls and reviewed deletion over real HTTP."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_http as fixture
from apps.openpine.cli import main
from apps.openpine.http_client import LocalClient
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import read_json, digest


class ResearchHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.HTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.HTTPTests.tearDownClass.__func__)
    call=classmethod(fixture.HTTPTests.call.__func__)

    def test_cli_uses_live_owner_and_export_matches_http(self):
        example=ROOT/'examples/search'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/'output.json'
            payload=read_json(example/'request.json');payload.pop('windows')
            payload.update(budget=1,space={'n':[1]},walkforward={'mode':'expanding','start_ms':60000,
                'train_ms':120000,'test_ms':60000,'step_ms':60000,'warmup_ms':60000,'folds':2,'max_jobs':4})
            request=root/'request.json';request.write_text(json.dumps(payload),encoding='utf-8')
            # No state directory or second coordinator may be opened by client mode.
            server='http://127.0.0.1:'+str(self.server.server_port)
            with patch('apps.openpine.walkforward_cli.local_manager',side_effect=AssertionError('Second owner.')):
                code=main(['walkforward','run','--server',server,'--request',str(request),
                    '--source',str(example/'v6.pine'),'--bars',str(ROOT/'examples/inputs/bars.json'),'--output',str(output)])
            state=read_json(output);self.assertEqual(code,0,state)
            self.assertEqual(state['status'],'succeeded',state)
            identifier=state['walkforward_id'];base=['walkforward','--server',server,'--id',identifier,'--output',str(output)]
            self.assertEqual(main([base[0],'report',*base[1:]]),0)
            report=read_json(output)
            http=self.call('/api/walkforwards/'+identifier+'/report')[1]
            self.assertEqual(report,http);self.assertEqual(report['sha256'],digest(report['report']))
            child=state['folds'][0]['search_id']
            with patch('apps.openpine.optimization_cli.local_manager',side_effect=AssertionError('Second search owner.')):
                self.assertEqual(main(['search','status','--id',child,'--server',server,'--output',str(output)]),0)
            selection=read_json(output)['selection_id']
            with patch('apps.openpine.experiment_cli.local_manager',side_effect=AssertionError('Second experiment owner.')):
                self.assertEqual(main(['experiment','status','--id',selection,'--server',server,'--output',str(output)]),0)
            self.assertEqual(self.call('/api/searches/'+child+'/pause',{})[0],400)
            self.assertEqual(self.call('/api/research/search/'+child+'/preview',{})[0],400)
            self.assertEqual(self.call('/api/walkforwards/'+identifier+'?limit=101')[0],400)
            self.assertEqual(main([base[0],'delete-preview',*base[1:]]),0)
            preview=read_json(output)
            self.assertEqual(self.call('/api/research/walkforward/'+identifier+'/delete',{'preview_id':'0'*64})[0],400)
            self.assertEqual(main([base[0],'delete-apply',*base[1:],'--preview-id',preview['preview_id']]),0)
            self.assertEqual(read_json(output)['files_removed'],0)
            self.assertEqual(self.call('/api/walkforwards/'+identifier)[0],400)
            for asset in ('research.js','walkforward.js'):
                self.assertEqual(self.call('/'+asset)[0],200)
                self.assertIn(('/'+asset).encode(),self.call('/')[1])

    def test_client_restricts_target_and_server_rejects_bad_policy(self):
        for url in ('https://127.0.0.1:80','http://example.org','http://127.0.0.1/a','http://user@localhost','http://localhost/#x'):
            with self.subTest(url=url),self.assertRaises(ValueError):LocalClient(url)
        self.assertEqual(self.call('/api/walkforwards',{})[0],400)
        self.assertEqual(self.call('/api/walkforwards?limit=2')[0],400)
        self.assertEqual(self.call('/api/research/search/'+'0'*32+'/delete',{})[0],400)
        self.assertEqual(self.call('/api/walkforwards',{},headers={'X-OpenPine-Token':'wrong'})[0],403)
