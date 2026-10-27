"""Feature detection stays finite before the independent IR admission budget."""
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from tools.specification import ROOT

class TargetClassificationTests(unittest.TestCase):
    def test_repeated_alias_dag_rejects_promptly_with_and_without_udf_all_legacy_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bounded.pine'
            for version in (1,6):
                for functions in (False,True):
                    text=f'//@version={version}\n'+('study' if version<5 else 'indicator')+'("Bounded")\na0="TEST:"\n'
                    text+=''.join(f'a{i}=a{i-1}+a{i-1}\n' for i in range(1,29))
                    text+='f() => "A"\nplot('+('security' if version<5 else 'request.security')+'(a28'+('+f()' if functions else '')+',"2",close))\n'
                    path.write_text(text,encoding='utf-8')
                    with self.subTest(version=version,functions=functions):
                        result=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','requirements','--source',str(path)],cwd=ROOT,
                            text=True,capture_output=True,encoding='utf-8',timeout=30)
                        self.assertEqual(result.returncode,2,result.stderr)
                        payload=json.loads(result.stdout);self.assertEqual(payload['diagnostics'][0]['code'],'REQUEST_TARGET')
                        self.assertIn('128 nodes',payload['diagnostics'][0]['message'])

if __name__=='__main__':unittest.main()
