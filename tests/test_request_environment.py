"""Private counters, timestamps, sparse observations and restart boundaries."""
import copy
import unittest
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from tools.request_environment_spec import profile,fixtures
from tools.conformance import run_cases
from tools.specification import ROOT
import test_request_expressions as legacy
from test_request_expressions import source,PRIMARY,HIGHER,events,Sink,mapped

class RequestEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_generated_overlay_and_independent_sparse_context_fixtures(self):
        self.assertEqual(loads((ROOT/'spec/request-environment-profile.json').read_bytes()),profile())
        for name,raw in fixtures().items():self.assertEqual((ROOT/'conformance/request-environment'/name).read_bytes(),raw)
        value=run_cases(cases_path='conformance/request-environment/cases.json')
        self.assertEqual((value['executed_programs'],value['expected_rejections']),(27,15))

    def test_counter_origin_history_and_every_historical_resume_cut_all_versions(self):
        helper=legacy.RequestExpressionTests();helper.session=self.session
        for v in range(1,7):
            program=self.session.compile(source(v,'bar_index+bar_index[1]'))
            expected=helper.run_program(program)
            self.assertEqual(expected.outputs[0].values,mapped((None,1.,3.,5.,7.,9.),v))
            for cut in range(1,12):
                first=helper.driver(program);saved=[]
                first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                resumed=helper.driver(program);resumed.restore(saved[0]);resumed.run()
                baseline=helper.driver(program);baseline.run()
                self.assertEqual(canonical(resumed.checkpoint()),canonical(baseline.checkpoint()))

    def test_requested_preview_counter_is_rolled_back_and_every_event_cut_restores(self):
        tape=events()
        for v in range(1,7):
            program=self.session.compile(source(v,'bar_index+time/120000'))
            def engine():
                out=Sink();return ReplayEngine(self.session,program,(PRIMARY,HIGHER),'primary',out),out
            full,expected=engine()
            for event in tape:
                before={k:canonical(r['evaluation'].checkpoint()) for k,r in full.requests.items() if r['evaluation'].count}
                full.apply(event)
                if event.kind!='close':
                    for key,raw in before.items():self.assertEqual(canonical(full.requests[key]['evaluation'].checkpoint()),raw)
            for cut in range(1,len(tape)):
                first,prefix=engine()
                for event in tape[:cut]:first.apply(event)
                saved=loads(canonical(first.checkpoint()));resumed,tail=engine();resumed.restore(saved)
                for event in tape[cut:]:resumed.apply(event)
                self.assertEqual(prefix.rows+tail.rows,expected.rows)
                self.assertEqual(canonical(resumed.checkpoint()),canonical(full.checkpoint()))

    def test_profile_drift_fails_closed_without_changing_old_bundles(self):
        from packages.contracts import request_environment as contract
        bad=copy.deepcopy(profile());bad['versions']['6']['policy']['history']='primary'
        from packages.compiler.semantic.specification import load_snapshot
        snapshot=load_snapshot()
        with patch.object(contract,'read_json',return_value=bad),self.assertRaises(ValueError):
            contract.admit(ROOT,snapshot,6)
        for expr in ('strategy.equity','barstate.islast ? 1 : 0','request.security("TEST:A","2",close)'):
            with self.assertRaises(OpenPineError):self.session.compile(source(6,expr))

    def test_tuple_keeps_counter_type_separate_from_timestamp_all_versions(self):
        helper=legacy.RequestExpressionTests();helper.session=self.session
        for v in range(1,7):
            api='security' if v<5 else 'request.security';decl='study' if v<5 else 'indicator'
            text=f'//@version={v}\n{decl}("tuple env")\nf(x) => [x,time]\n[i,t]={api}("TEST:EXPRESSIONS","2",f(bar_index))\nplot(i)\nplot(t)'
            result=helper.run_program(self.session.compile(text))
            self.assertEqual(result.outputs[0].values,mapped(tuple(range(6)),v))
            self.assertEqual(result.outputs[1].values,mapped(tuple(i*120000 for i in range(6)),v))

if __name__=='__main__':unittest.main()
