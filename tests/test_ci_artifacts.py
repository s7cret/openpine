"""Qualification rejects missing coverage, mixed source and substituted wheels."""
import copy,hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from packages.contracts.wire import digest
from tools.ci_artifacts import audit_receipts,validate_source,verify_record,verify_wheel_sources,PLATFORMS
from tools.installed_suites import SUITES,GROUPS
from tools.release_gate import specification_checks,validate_qualification
from tools.test_shards import partition,weights
from tools.specification import ROOT

def receipts():
    names=[f'test_{c}.py' for c in 'abcdef'];source={'sha256':'s','modules':names,'python':'3.14.8','conformance':{'cases':3,'executed':2,'rejected':1,'corpora':[]}}
    wheel={'source':source,'suites':list(SUITES),'wheel_sha256':'w'};specs=[];shards=[];installed=[];cases=set()
    for os in PLATFORMS:
        spec={'schema_version':'openpine.gate-receipt.2','platform':os,'status':'passed','section':'specification','fast':False,'assigned_modules':[],'source':source,'checks':[{'check':n,'exit_code':0} for n in ['architecture',*[n for n,_ in specification_checks()]]],'conformance':source['conformance']};specs.append(spec)
        for i,assigned in enumerate(partition(names,6,*weights(os))):
            results=[]
            for n in assigned:
                case=n[:-3]+'.Case.test_one';cases.add(case);results.append({'module':n,'successful':True,'tests':1,'cases':[{'test':case}]})
            shards.append({'platform':os,'status':'passed','section':'tests','fast':False,'source':source,'qualification_sha256':digest(spec),'shard_count':6,'shard_index':i,'assigned_modules':assigned,'checks':[{'check':n,'exit_code':0} for n in ['architecture',*assigned]],'module_results':results})
        for suite in SUITES:
            result={'installed_suite':suite,'status':'passed','python':'3.14.8','network_required':False,'root':'/installed'}
            if suite=='core':result['completed_core']=True
            else:result['completed_helpers']=list(GROUPS[suite])
            installed.append({'schema_version':'openpine.installed-receipt.1','platform':os,'status':'passed','source':source,'suite':suite,'wheel_sha256':'w','result':result})
    return specs,shards,installed,wheel,source,cases

class CIArtifactTests(unittest.TestCase):
    def test_complete_disjoint_two_platform_coverage_and_one_wheel_accepts(self):
        result=audit_receipts(*receipts());self.assertEqual(result['tests_per_os'],6);self.assertEqual(result['wheel_builds'],1);self.assertEqual(result['specification_runs'],2)
    def test_missing_and_duplicate_shards_never_qualify(self):
        for mode in ('missing','duplicate'):
            values=list(receipts());values[1]=values[1][:-1] if mode=='missing' else values[1][:-1]+[values[1][0]]
            with self.assertRaises(AssertionError):audit_receipts(*values)
    def test_missing_duplicate_or_foreign_test_cases_fail(self):
        for case in ('test_a.Case.other','test_b.Case.test_one'):
            values=list(receipts());values[1][0]['module_results'][0]['cases'][0]['test']=case
            with self.assertRaises(AssertionError):audit_receipts(*values)
    def test_fast_receipts_foreign_source_and_wrong_parent_are_rejected(self):
        for field,value in (('fast',True),('qualification_sha256','changed'),('source',{})):
            values=list(receipts());values[1][0][field]=value
            with self.assertRaises(AssertionError):audit_receipts(*values)
        values=receipts();bad=copy.deepcopy(values[0][0]);bad['checks'].pop()
        with self.assertRaises(AssertionError):validate_qualification(bad,values[4])
    def test_missing_helpers_and_substituted_wheel_never_qualify(self):
        values=list(receipts());values[2][0]['wheel_sha256']='another'
        with self.assertRaises(AssertionError):audit_receipts(*values)
        values=list(receipts());entry=next(r for r in values[2] if r['suite']!='core');entry['result']['completed_helpers']=[]
        with self.assertRaises(AssertionError):audit_receipts(*values)
    def test_source_checksum_and_stale_identity_are_rejected(self):
        value={'python':'3.14.8'};value['sha256']=digest(value)
        with patch('tools.ci_artifacts.source_identity',return_value=value):validate_source(value)
        bad={**value,'python':'3.14.7'}
        with self.assertRaises(AssertionError):validate_source(bad)
    def test_record_rejects_unrecorded_entries_and_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fake.whl'
            for name in ('evil.py','../evil.py'):
                with zipfile.ZipFile(path,'w') as z:z.writestr(name,b'x');z.writestr('fake.dist-info/RECORD','fake.dist-info/RECORD,,\n')
                with self.assertRaises(AssertionError):verify_record(path)
    def test_declared_source_hash_does_not_authorize_different_archive_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fake.whl'
            with zipfile.ZipFile(path,'w') as z:z.writestr('packages/runtime/engine.py',b'changed')
            with self.assertRaises(AssertionError):verify_wheel_sources(path,{'files':{'packages/runtime/engine.py':hashlib.sha256(b'original').hexdigest()}})
    def test_ci_builds_one_wheel_and_never_rebuilds_in_installation_matrix(self):
        workflow=(ROOT/'.github/workflows/r6-specification.yml').read_text()
        self.assertEqual(workflow.count('python -m tools.ci_artifacts build '),1)
        self.assertEqual(workflow.count('--section specification'),1);self.assertEqual(workflow.count('--section tests'),1)
        self.assertIn('python -m tools.install_check --wheel-dir',workflow);self.assertIn('merge-multiple: false',workflow)
        self.assertIn('needs: [feedback, build, qualification, tests, installation, recovery]',workflow)
        self.assertNotIn('needs: feedback',workflow.split('  qualification:')[0])
        self.assertIn('shard: [0, 1, 2, 3, 4, 5]',workflow)
        self.assertIn('--require-recovery',workflow)
        self.assertIn('max-parallel: 12',workflow.split('  installation:')[1])
        self.assertIn('needs: build',workflow.split('  recovery:')[1].split('  acceptance:')[0])
    def test_real_wheel_preserves_execution_and_full_build_identity(self):
        from tools.ci_artifacts import build
        from packages.execution.identity import build_identity,implementation_hash
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=build(root/'wheel');path=root/'wheel'/manifest['wheel'];verify_record(path)
            with zipfile.ZipFile(path) as archive:
                self.assertFalse(any(n.startswith('conformance/results/') for n in archive.namelist()))
                archive.extractall(root/'installed')
            self.assertEqual(build_identity(root/'installed'),build_identity())
            self.assertEqual(implementation_hash(root/'installed'),implementation_hash())

if __name__=='__main__':unittest.main()
