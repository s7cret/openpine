"""Every qualified whole-program example gets an honest metadata explanation."""
import unittest
from apps.openpine.corpus import Corpus,compile_case
from apps.openpine.passport import describe
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.execution.session import Session
from packages.contracts.wire import loads

class PassportCorpusTests(unittest.TestCase):
    def test_all_fifty_executions_and_six_rejections_retain_their_boundaries(self):
        corpus=Corpus();session=Session(profile_id=HISTORICAL_PROFILE_ID);admitted=rejected=0
        for case in corpus.cases.values():
            with self.subTest(case=case['id']):
                compilation,program=compile_case(corpus,case);self.assertTrue(compilation['expected'])
                if program is None:rejected+=1;continue
                config=loads(corpus.files[case['id']]['context_config']) if 'context_config' in corpus.files[case['id']] else None
                data=loads(corpus.files[case['id']]['bars'])
                from dataclasses import asdict
                from packages.contracts.contexts import MarketContext
                primary=config['primary_context'] if config else asdict(MarketContext(data['context_id'],data['symbol'],data['timeframe'],'uploaded-file.1'))
                report=describe(program,session=session,inputs=case['inputs'],primary_context=primary)
                self.assertEqual(report['status'],'described');self.assertEqual(report['program']['language_version'],case['version'])
                self.assertFalse(report['readiness']['jobs_submitted']);self.assertFalse(report['evidence']['whole_program_execution_verified'])
                self.assertTrue(all(not r['data_checked'] and not r['resources_reserved'] for r in report['modes']))
                self.assertEqual(next(r for r in report['modes'] if r['mode']=='history')['status'],'configuration_admitted')
                if case['family'] in ('pending','partial'):
                    self.assertEqual(next(r for r in report['modes'] if r['mode']=='replay')['status'],'rejected_configuration')
                    self.assertEqual(next(r for r in report['modes'] if r['mode']=='observed')['status'],'configuration_admitted')
                admitted+=1
        self.assertEqual((admitted,rejected),(50,6))

if __name__=='__main__':unittest.main()
