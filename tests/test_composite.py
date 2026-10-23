"""Independent numerical and type expectations for composite calls v1..v6."""
import copy
from dataclasses import replace
from decimal import Decimal,localcontext
import math
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData,OpenPineError,ProgramIR,TupleType,ValueType
from packages.contracts.codec import encode,decode
from packages.execution.session import Session
from packages.runtime.history import StorageBudget
from packages.runtime.series.composite import MomentsState,MACDState,BBState

VALUES=(10.,12.,None,9.,15.,13.,16.,8.,17.,17.,11.,20.)
DATA=HistoricalData('composite','TEST:COMPOSITE','1',tuple(HistoricalBar(i*60000,x,x,None if x is None else x+2,None if x is None else x-2,1.) for i,x in enumerate(VALUES)),schema_version='openpine.historical-bars.2')


def source(version,body):
    return f'//@version={version}\n'+('study' if version<5 else 'indicator')+'("composite")\n'+body+'\n'


def moments_oracle(values,length,biased=True):
    window=[];means=[];variances=[];deviations=[]
    with localcontext() as context:
        context.prec=80
        for value in values:
            if value is not None:window.append(Decimal.from_float(value));window=window[-length:]
            if len(window)<length:means.append(None);variances.append(None);deviations.append(None);continue
            mean=sum(window)/length
            variance=None if not biased and length==1 else sum((v-mean)**2 for v in window)/(length if biased else length-1)
            means.append(float(mean));variances.append(None if variance is None else float(variance));deviations.append(None if variance is None else float(variance.sqrt()))
    return means,variances,deviations


def macd_oracle(values,fast,slow,signal,version):
    with localcontext() as context:
        context.prec=60;states=[None,None,None];pending=[[],[],[]]
        def ema(i,value,length):
            if value is None:return states[i]
            if states[i] is None:
                if version<5:
                    pending[i].append(value)
                    if len(pending[i])<length:return None
                    states[i]=sum(pending[i])/length
                else:states[i]=value
            else:
                alpha=Decimal(2)/(length+1);states[i]=alpha*value+(1-alpha)*states[i]
            return states[i]
        rows=[[],[],[]]
        for value in values:
            value=None if value is None else Decimal.from_float(value)
            a,b=ema(0,value,fast),ema(1,value,slow)
            main=None if a is None or b is None else a-b
            average=ema(2,main,signal);hist=None if main is None or average is None else main-average
            for out,item in zip(rows,(main,average,hist)):out.append(None if item is None else float(item))
        return rows


class CompositeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def close_values(self,actual,expected):
        self.assertEqual(len(actual),len(expected))
        for a,b in zip(actual,expected,strict=True):
            if b is None:self.assertIsNone(a)
            else:self.assertTrue(math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12),(a,b))

    def test_all_versions_macd_bb_and_statistics_match_independent_decimal(self):
        means,var,sd=moments_oracle(VALUES,3)
        for version in range(1,7):
            p='ta.' if version>=5 else ''
            body=f'[m,s,h]={p}macd(close,2,4,3)\n[b,u,l]={p}bb(close,3,2)\nplot(m)\nplot(s)\nplot(h)\nplot(b)\nplot(u)\nplot(l)\nplot({p}variance(close,3))\nplot({p}stdev(close,3))'
            program,result=self.session.run(source(version,body),data=DATA)
            self.assertEqual(program.schema_version,'openpine.typed-ir.7')
            expected=[*macd_oracle(VALUES,2,4,3,version),means,
                [None if m is None else m+2*d for m,d in zip(means,sd)],
                [None if m is None else m-2*d for m,d in zip(means,sd)],var,sd]
            for output,row in zip(result.outputs,expected,strict=True):self.close_values(output.values,row)

    def test_unbiased_singleton_dynamic_bias_and_legacy_arity(self):
        for version in (5,6):
            _,r=self.session.run(source(version,'plot(ta.variance(close,1,false))\nplot(ta.stdev(close,3,false))'),data=DATA)
            self.assertTrue(all(v is None for v in r.outputs[0].values));self.close_values(r.outputs[1].values,moments_oracle(VALUES,3,False)[2])
            _,r=self.session.run(source(version,'plot(ta.variance(close,3,close>12))'),data=DATA)
            population=moments_oracle(VALUES,3)[1];sample=moments_oracle(VALUES,3,False)[1]
            self.close_values(r.outputs[0].values,[population[i] if x is not None and x>12 else sample[i] for i,x in enumerate(VALUES)])
        for version in range(1,5):
            with self.assertRaises(OpenPineError):self.session.compile(source(version,'plot(stdev(close,3,false))'))

    def test_large_offset_small_spread_and_extreme_finite_deviation(self):
        values=[1e15+x for x in (0.,.25,.5,.75,.125,.375)]
        state=MomentsState(StorageBudget());expected=moments_oracle(values,3)
        for index,value in enumerate(values):
            state.update(value,3);self.assertEqual(state.variance(),expected[1][index]);self.assertEqual(state.variance(root=True),expected[2][index])
        state=MomentsState(StorageBudget());state.update(1e308,2);state.update(-1e308,2)
        self.assertEqual(state.variance(root=True),1e308)
        with self.assertRaises(ValueError):state.variance()

    def test_tuple_call_once_discard_independent_instances_and_scalar_history(self):
        body='pair(x) =>\n    var float counter=0\n    counter:=counter+1\n    [x,counter,counter*10]\n[a,_,c]=pair(close)\n[_,b,_]=pair(1)\nplot(c)\nplot(b)\nplot(a[1])'
        for version in range(1,7):
            _,r=self.session.run(source(version,body),data=DATA)
            self.assertEqual(r.outputs[0].values,tuple(float((i+1)*10) for i in range(len(VALUES))))
            self.assertEqual(r.outputs[1].values,tuple(float(i+1) for i in range(len(VALUES))))
            self.assertEqual(r.outputs[2].values,(None,*VALUES[:-1]))

    def test_value_if_tuple_promotion_qualifiers_and_skipped_calls(self):
        body='pair(x) =>\n    if x>12\n        [x,2.0]\n    else\n        [1,3]\n[a,b]=pair(close)\nplot(a)\nplot(b)'
        for version in range(1,7):
            _,r=self.session.run(source(version,body),data=DATA)
            self.assertEqual(r.outputs[0].values,tuple(x if x is not None and x>12 else 1. for x in VALUES))
            self.assertEqual(r.outputs[1].values,tuple(2. if x is not None and x>12 else 3. for x in VALUES))

    def test_ir_roundtrip_rejects_forged_types_bindings_and_schema(self):
        p=self.session.compile(source(6,'[a,b]=[close,1]\nplot(a)\nplot(b)'))
        self.assertEqual(decode(encode(p),ProgramIR),p)
        node=p.statements[1];tuple_node=node.expression
        bad=(replace(tuple_node,value_type=TupleType((ValueType('float','series'),))),
             replace(tuple_node,elements=(tuple_node,tuple_node),value_type=TupleType((ValueType('float','series'),)*2)))
        for expr in bad:
            with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,statements=(p.statements[0],replace(node,expression=expr),*p.statements[2:])))
        for change in (replace(node,bindings=(node.bindings[0],)*2),replace(node,bindings=node.bindings[:1])):
            with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,statements=(p.statements[0],change,*p.statements[2:])))
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.6'))
        raw=encode(p);raw['statements'][1]['expression']['value_type']['elements'][0]['base']='tuple'
        with self.assertRaises(ValueError):decode(raw,ProgramIR)

    def test_reject_nested_products_storage_parameters_invalid_targets_and_lengths(self):
        bodies=('[a,b]=[[1,2],3]\nplot(1)','[a,b,c]=[1,2]\nplot(1)',
            'value=[1,2]\nplot(1)','[a,a]=[1,2]\nplot(a)','[_,_]=[1,2]\nplot(_)',
            'f(x)=>x\n[a,b]=f([1,2])\nplot(a)','[a,b]=[close,1]\n[a,b]=[1,2]\nplot(a)',
            '[a,b]=ta.bb(close,2,1)\nplot(a)','plot(ta.stdev(close,0))',
            '[a,b,c]=ta.macd(close,int(close),3,2)\nplot(a)',
            '[a,b]=true ? [1,2] : [3,4]\nplot(a)')
        for body in bodies:
            with self.subTest(body=body),self.assertRaises(OpenPineError):self.session.compile(source(6,body)) if 'stdev(close,0)' not in body else self.session.run(source(6,body),data=DATA)


if __name__=='__main__':unittest.main()
