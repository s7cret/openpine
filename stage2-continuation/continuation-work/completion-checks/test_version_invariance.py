import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'sources/pine2ast/pine2ast/libraries/version_compatibility.py'
spec=importlib.util.spec_from_file_location('_openpine_scalar_proof', MODULE)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)


def lib(body,version=5):
    return f'//@version={version}\nlibrary("Invariant")\n'+body+'\n'

class ProofTests(unittest.TestCase):
    def test_versions_and_exact_bytes(self):
        for v in (5,6):
            for expr in ('x + 1', 'x - 1', 'x * 3', 'x % 2', '-x', '+x', '42'):
                with self.subTest(v=v,expr=expr):
                    source=lib('export f(int x) => '+expr,v)
                    proof=m.prove_version_invariant_library(source)
                    self.assertEqual(proof['source_pine_version'],v)
                    self.assertEqual(proof['compatible_consumer_versions'],[5,6])
                    m.verify_version_invariant_proof(source,proof)
                    with self.assertRaises(m.VersionSensitiveLibrary):
                        m.verify_version_invariant_proof(source+'\n',proof)
    def test_local_acyclic_calls(self):
        source=lib('helper(float x) => x * 2.0\nexport f(float x) => helper(x) + 1.0')
        proof=m.prove_version_invariant_library(source)
        self.assertEqual(proof['functions']['f']['calls'],['helper'])
        self.assertEqual(proof['functions']['f']['return_type'],'float')
    def test_string_comment_scanning(self):
        proof=m.prove_version_invariant_library(lib('export f(string x) => x + "http://example" // comment'))
        self.assertEqual(proof['functions']['f']['return_type'],'string')
    def test_sensitive_constructs_are_never_converted(self):
        for body in ('export f() => 5 / 2','export f() => na',
            'export f() => close','export f() => true','export f(int x) => x > 0',
            'export f(int x) => x[1]', 'export f(int x) => x if x else 0',
            'export f(int x) => abs(x)', 'export f(int x = 1) => x',
            'export f(int x) => f(x)', 'var int n = 0\nexport f() => 1',
            'export f(int x) =>\n    x + 1',
            'import user/Other/1 as other\nexport f() => other.f()',
            'export f(int x) => x\nexport f(float x) => x',
            'export f() => 1e999', 'export f() => None',
            'export f(bool x) => x', 'export f() => __import__("os")'):
            with self.subTest(body=body):
                with self.assertRaises(m.VersionSensitiveLibrary):
                    m.prove_version_invariant_library(lib(body))
    def test_rehashed_forgery_is_rejected(self):
        source=lib('export f(int x) => x + 1')
        proof=m.prove_version_invariant_library(source)
        proof['compatible_consumer_versions']=[4,5,6]
        proof.pop('content_hash')
        proof['content_hash']=m._canonical(proof)
        with self.assertRaises(m.VersionSensitiveLibrary):
            m.verify_version_invariant_proof(source,proof)
    def test_wrong_versions_and_directives(self):
        for source in (lib('export f() => 1',4),lib('export f() => 1')+'//@version=6\n',
                       'library("X")\nexport f() => 1\n'):
            with self.subTest(source=source):
                with self.assertRaises(m.VersionSensitiveLibrary):
                    m.prove_version_invariant_library(source)

if __name__=='__main__': unittest.main(verbosity=2)
