import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools.installed_suites import GROUPS,SUITES,verify_helpers
from tools.specification import ROOT


class InstalledSuiteTests(unittest.TestCase):
    def test_full_legacy_and_new_inventory_is_disjoint_and_on_both_ci_platforms(self):
        expected={'input','search','research','protection','technical','launch','loop','array',
            'environment','signal','context_graph','partial','pending','lot','order_group','trailing','economics','risk','performance','resilience','observed'}
        helpers=[h for rows in GROUPS.values() for h in rows]
        self.assertEqual(set(helpers),expected);self.assertEqual(len(helpers),len(expected))
        workflow=(ROOT/'.github/workflows/r6-specification.yml').read_text()
        suites=re.search(r'        suite: \[([^\]]+)\]',workflow).group(1).split(', ')
        self.assertEqual(suites,list(SUITES))
        self.assertEqual(workflow.count('os: [ubuntu-latest, windows-latest]'),3)
        self.assertIn('check: [gate-0, gate-1, gate-2]',workflow)

    def test_every_requested_helper_executes_and_research_receives_server_url(self):
        seen=[]
        def module(name):
            helper=name.removeprefix('tools.installed_').removesuffix('_smoke')
            def verify(*args):
                self.assertEqual(args[:3],('http','cli','work'))
                self.assertEqual(args[3:],('base',) if helper in ('research','resilience') else ())
                seen.append(helper);return {helper+'_checked':True}
            return SimpleNamespace(verify=verify)
        with patch('tools.installed_suites.importlib.import_module',side_effect=module):
            value=verify_helpers('http','cli','work','base',tuple(GROUPS))
        self.assertEqual(seen,[h for hs in GROUPS.values() for h in hs])
        self.assertEqual(value['completed_helpers'],seen)
        self.assertTrue(all(value[h+'_checked'] for h in seen))

    def test_overlapping_receipts_and_unknown_suites_fail_closed(self):
        with patch('tools.installed_suites.importlib.import_module',return_value=SimpleNamespace(verify=lambda *a:{'duplicate':True})):
            with self.assertRaisesRegex(AssertionError,'overlap'):verify_helpers(None,None,None,None,('research',))
        with self.assertRaises(ValueError):verify_helpers(None,None,None,None,('missing',))


if __name__=='__main__':unittest.main()
