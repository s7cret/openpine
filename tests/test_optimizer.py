"""Independent expected scores, finite admission, and deterministic search semantics."""
from dataclasses import replace
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.optimization import evaluation_window,split_windows,metric_spec
from packages.execution.session import Session
from packages.optimizer.search import candidates
from packages.optimizer.metrics import evaluate,rank
from test_inputs import code


class OptimizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_six_versions_share_typed_finite_space_and_stable_random_sequence(self):
        for version in range(1,7):
            program=self.session.compile(code(version))
            options={'method':'grid','budget':6,'seed':42}
            result=candidates(program,{'length':{'min':1,'max':3,'step':1},'scale':[1,2]},**options)
            self.assertEqual([(s['values']['input:length'],s['values']['input:scale']) for s in result['candidates']],[(1,1.),(1,2.),(2,1.),(2,2.),(3,1.),(3,2.)])
            first=candidates(program,{'length':[1,2,3],'scale':[1,2]},method='random',budget=4,seed=42)
            second=candidates(program,{'scale':[1,2],'input:length':[1,2,3]},method='random',budget=4,seed=42)
            self.assertEqual(first,second);self.assertEqual(len({s['sha256'] for s in first['candidates']}),4)
            full=candidates(program,{'length':[1,2,3]},method='random',budget=3,seed=0)
            self.assertEqual({s['values']['input:length'] for s in full['candidates']},{1,2,3})

    def test_space_rejects_duplicates_overflow_unbounded_and_implicit_ui_step(self):
        program=self.session.compile(code(6));base={'method':'grid','budget':256,'seed':0}
        for space in ({'length':[1,1]},{'length':[True]},{'length':{'min':1,'max':10}}, {'length':{'min':1,'max':3,'step':0}}, {'length':{'min':1.5,'max':3,'step':1}}, {'length':list(range(257))}, {'length':[1],'input:length':[2]}, {'unknown':[1]}, {'scale':[1,1.]}, {'scale':[float('inf')]}):
            with self.subTest(space=str(space)[:80]),self.assertRaises((ValueError,OpenPineError)):
                candidates(program,space,**base)
        for extra in ({'budget':0},{'seed':True},{'method':'bayesian'},{'method':'random','budget':4}):
            with self.assertRaises(ValueError):candidates(program,{'length':[1,2,3]},**{**base,**extra})
        program=self.session.compile('//@version=6\nindicator("step")\nn=input.int(2,step=2)\nplot(n)')
        result=candidates(program,{'n':{'min':1,'max':3,'step':1}},**base)
        self.assertEqual([s['values']['input:n'] for s in result['candidates']],[1,2,3])

    def test_decimal_float_axes_and_exact_int64(self):
        program=self.session.compile('//@version=6\nindicator("exact")\nn=input.int(9007199254740993)\nx=input.float(0.1)\nplot(x)')
        result=candidates(program,{'x':{'min':'0.1','max':'0.3','step':'0.1'},'n':[{'$int':'9007199254740993'}]},method='grid',budget=3,seed=0)
        self.assertEqual([s['values']['input:x'] for s in result['candidates']],[.1,.2,.3])
        self.assertEqual(result['candidates'][0]['values']['input:n'],9007199254740993)

    def test_manual_broker_scores_skip_warmup_and_apply_constraints(self):
        window={'warmup_start_ms':0,'score_start_ms':2,'end_ms':5}
        summary={'simulation':{'policy':{'initial_capital':'100'}}}
        events=[{'kind':'mark','time':t,'value':{'equity':e,'trades':tr,'commission':'0.25'}} for t,e,tr in [(0,'999',0),(1,'1',0),(2,'110',0),(3,'95',1),(4,'108',2),(5,'0',2)]]
        objective={'metric':'net_profit','direction':'maximize'}
        result=evaluate(summary,window,objective,[],events=events)
        self.assertEqual(result['metrics'],{'net_profit':'8','max_drawdown':'15','trades':'2','commission':'0.25'})
        self.assertEqual(result['score'],'8');self.assertEqual(result['status'],'eligible')
        result=evaluate(summary,window,objective,[{'metric':'trades','operator':'gte','value':'3'}],events=events)
        self.assertEqual(result['status'],'constraint_failed');self.assertIsNone(result['score'])

    def test_indicator_missing_values_output_choice_and_exact_tie_order(self):
        window={'warmup_start_ms':0,'score_start_ms':1,'end_ms':4}
        summary={'outputs':[{'output_id':'A'},{'output_id':'B'}]}
        rows=[(i,i,'A',v) for i,v in enumerate((999,None,2.,4.,999))]+[(i,i,'B',v) for i,v in enumerate((999,3.,6.,9.,999))]
        spec={'metric':'output_mean','output_index':1,'direction':'maximize'}
        result=evaluate(summary,window,spec,[],rows=rows)
        self.assertEqual(result['score'],'6.0')
        self.assertEqual(evaluate(summary,window,spec,[],rows=[])['status'],'missing_metric')
        values=[{'ordinal':i,'assessment':{'status':'eligible','score':score}} for i,score in [(3,'1.00000000000000000000000000001'),(2,'1.00000000000000000000000000002'),(1,'1.00000000000000000000000000002')]]
        self.assertEqual([r['ordinal'] for r in rank(values,'maximize')],[1,2,3])
        self.assertEqual([r['ordinal'] for r in rank(list(reversed(values)),'minimize')],[3,1,2])

    def test_closed_temporal_and_metric_contracts(self):
        window={'warmup_start_ms':0,'score_start_ms':10,'end_ms':20}
        self.assertEqual(evaluation_window(window),window)
        with self.assertRaises(ValueError):split_windows({'selection':window,'holdout':{**window,'score_start_ms':19,'end_ms':30}})
        for value in ({'metric':'output_mean','direction':'maximize'}, {'metric':'unknown','direction':'maximize'},{'metric':'trades','direction':'minimize','output_index':0}):
            with self.assertRaises(ValueError):metric_spec(value,objective=True)


if __name__=='__main__':unittest.main()
