"""Independent changing-window oracles, bounded state and versioned admission."""
import copy,math,random,unittest
from dataclasses import asdict,replace,fields,is_dataclass
from fractions import Fraction
from packages.contracts import HISTORICAL_PROFILE_ID,ProgramIR,OpenPineError,CallIR
from packages.contracts.codec import encode,decode
from packages.contracts.contexts import ContextBinding
from packages.contracts.wire import read_json,digest,canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.marketdata import data_identity
from packages.runtime.history import StorageBudget
from packages.runtime.series import KernelError
from packages.runtime.series.dynamic_windows import PrefixWindow,RangeWindow,export_window,restore_window,fork_window,HORIZON
from tools.dynamic_windows_spec import oracle,profile,examples,corpus
from tools.specification import ROOT
from test_composite import DATA,VALUES

def code(v,body):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Windows")\n'+body
def body(v):
    p='' if v<5 else 'ta.';s='sum' if v<5 else 'math.sum'
    return 'n=bar_index%4+1\n'+''.join(f'plot({f}(close,n))\n' for f in (p+'sma',s,p+'wma',p+'highest',p+'lowest',p+'variance',p+'stdev'))
def rewrite(value,fn):
    if isinstance(value,CallIR):value=fn(value)
    if is_dataclass(value):return replace(value,**{f.name:rewrite(getattr(value,f.name),fn) for f in fields(value) if f.init})
    if type(value) is tuple:return tuple(rewrite(x,fn) for x in value)
    return value

class DynamicWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def close(self,a,b):
        self.assertEqual(len(a),len(b))
        for x,y in zip(a,b,strict=True):
            if y is None:self.assertIsNone(x)
            else:self.assertTrue(math.isclose(x,y,rel_tol=1e-12,abs_tol=1e-12),(x,y))
    def test_random_statistics_match_independent_fraction_lists(self):
        rng=random.Random(127);values=[None if i%11==0 else rng.randint(-100,100)/8 for i in range(300)];lengths=[rng.randint(1,31) for _ in values]
        expected=oracle(values,lengths)
        for kind,column in [('window.sum',1),('window.wma',2),('window.moments',5)]:
            s=PrefixWindow(StorageBudget(),kind);self.close([s.update(x,n) for x,n in zip(values,lengths)],expected[column])
        s=PrefixWindow(StorageBudget(),'window.moments');self.close([s.update(x,n,root=True) for x,n in zip(values,lengths)],expected[6])
    def test_unbiased_singleton_na_and_length_change_requeries_existing_samples(self):
        s=PrefixWindow(StorageBudget(),'window.moments')
        self.assertIsNone(s.update(2.,2,False));self.assertEqual(s.update(4.,2,False),2.)
        self.assertEqual(s.update(None,1,True),0.);self.assertIsNone(s.update(None,1,False))
        self.assertIsNone(s.update(8.,2,None));self.assertEqual(s.update(None,3,False),float(Fraction(28,3)))
    def test_extrema_match_reached_call_windows_with_holes_and_growing_lengths(self):
        rng=random.Random(55);values=[None if i%5==0 else float(rng.randrange(-20,21)) for i in range(250)];lengths=[rng.choice((1,2,7,31,100000)) for _ in values]
        for kind,fn in [('window.highest',max),('window.lowest',min)]:
            s=RangeWindow(StorageBudget(),kind);history=[]
            for x,n in zip(values,lengths):
                history.append(x);xs=[v for v in history[-n:] if v is not None]
                self.assertEqual(s.update(x,n),fn(xs) if xs else None)
    def test_shrinking_keeps_samples_needed_for_later_expansion(self):
        for kind,expected in [('window.sum',11.),('window.wma',float(Fraction(16,6)))]:
            s=PrefixWindow(StorageBudget(),kind)
            for x,n in [(8.,1),(1.,1),(2.,3)]:result=s.update(x,n)
            self.assertEqual(result,expected)
        s=RangeWindow(StorageBudget(),'window.highest');s.update(8.,1);s.update(1.,1);self.assertEqual(s.update(2.,3),8.)
    def test_horizon_wrap_preserves_maximum_supported_window(self):
        s=PrefixWindow(StorageBudget(),'window.wma');history=[]
        high=RangeWindow(StorageBudget(),'window.highest')
        for i in range(HORIZON+5):
            x=99999. if i==0 else float(i%13);history.append(x);s.update(x,1);high.update(x,1)
        xs=history[-HORIZON:];expected=sum((i+1)*Fraction(x) for i,x in enumerate(xs))/sum(range(1,HORIZON+1))
        self.assertEqual(s.update(None,HORIZON),float(expected));self.assertEqual(high.update(None,HORIZON),12.)
        self.assertEqual([len(r.items) for r in s.rings],[HORIZON+1]*2)
        kind,value=export_window(s);self.assertLess(len(canonical(value)),4*1024**2)
        restored=restore_window(kind,json_roundtrip(value),StorageBudget(),s.calls)
        self.assertEqual(export_window(restored),export_window(s))
    def test_invalid_lengths_fail_before_any_sample_or_counter_update(self):
        for s in (PrefixWindow(StorageBudget(),'window.sum'),RangeWindow(StorageBudget(),'window.highest')):
            s.update(3.,1);original=export_window(s)
            for n in (None,False,0,-1,100001,1.0,'2'):
                with self.assertRaisesRegex(KernelError,'defined int'):s.update(7.,n)
                self.assertEqual(export_window(s),original)
    def test_shared_storage_budget_is_checked_before_allocation(self):
        b=StorageBudget(limit=HORIZON*2-1);s=RangeWindow(b,'window.highest')
        with self.assertRaisesRegex(KernelError,'storage'):s.update(1.,1)
        self.assertEqual((b.cells,s.nodes,s.calls),(0,[],0))
        b=StorageBudget(limit=3);s=PrefixWindow(b,'window.wma')
        with self.assertRaisesRegex(KernelError,'storage'):s.update(1.,1)
        self.assertEqual((b.cells,s.count,s.calls),(2,0,0))
    def test_exact_moments_large_offset_overflow_and_finite_root(self):
        s=PrefixWindow(StorageBudget(),'window.moments');xs=[1e12+.25,1e12+.5,1e12+1.]
        for x in xs:actual=s.update(x,3)
        mean=sum(map(Fraction,xs))/3;self.assertEqual(actual,float(sum((Fraction(x)-mean)**2 for x in xs)/3))
        s=PrefixWindow(StorageBudget(),'window.moments');s.update(-1e308,2)
        self.assertEqual(s.update(1e308,2,root=True),1e308)
        with self.assertRaises(KernelError):s.update(None,2)
        s=PrefixWindow(StorageBudget(),'window.sum');s.update(1e308,2)
        with self.assertRaises(KernelError):s.update(1e308,2)
    def test_all_states_roundtrip_and_continue_after_length_changes(self):
        for kind in ('window.sum','window.wma','window.moments','window.highest','window.lowest'):
            b=StorageBudget();s=RangeWindow(b,kind) if 'highest' in kind or 'lowest' in kind else PrefixWindow(b,kind)
            for x,n in [(7.,1),(1.,1),(None,3),(9.,2)]:s.update(x,n)
            k,v=export_window(s);other=StorageBudget();restored=restore_window(k,json_roundtrip(v),other,s.calls)
            self.assertEqual(export_window(restored),export_window(s));self.assertEqual(b.cells,other.cells)
            for x,n in [(None,1),(2.,5),(10.,2),(3.,6)]:self.assertEqual(restored.update(x,n),s.update(x,n))
    def test_malformed_prefixes_counts_and_coherent_checksum_changes_are_rejected(self):
        for kind in ('window.sum','window.wma','window.moments'):
            s=PrefixWindow(StorageBudget(),kind)
            for x in (1.,2.,3.):s.update(x,2)
            _,original=export_window(s)
            mutations=[lambda v:v.update(calls=True),lambda v:v.update(length=100001),lambda v:v.update(count=5),lambda v:v['samples'].__setitem__(1,1.),lambda v:v['anchors'].__setitem__(0,1)]
            if len(s.rings)==2:mutations.append(lambda v:v['totals'].__setitem__(1,v['totals'][1]+1))
            for mutate in mutations:
                bad=copy.deepcopy(original);mutate(bad)
                with self.subTest(kind=kind),self.assertRaises(ValueError):restore_window(kind,bad,StorageBudget(),3)
    def test_preview_overlays_do_not_copy_or_mutate_confirmed_history(self):
        for kind in ('window.sum','window.wma','window.moments','window.highest','window.lowest'):
            s=RangeWindow(StorageBudget(),kind) if kind in ('window.highest','window.lowest') else PrefixWindow(StorageBudget(),kind)
            for x in range(30):s.update(float(x),x%5+1)
            before=export_window(s);clone=fork_window(s,copy.copy(s.budget));clone.update(100.,20)
            self.assertEqual(export_window(s),before)
            if isinstance(s,RangeWindow):self.assertIs(clone.nodes.base,s.nodes);self.assertLessEqual(len(clone.nodes.changed),19)
            else:
                for a,b in zip(clone.rings,s.rings):self.assertIs(a.base,b);self.assertEqual(len(a.changed),1)
    def test_fixed_contracts_and_simple_length_paths_are_preserved_all_versions(self):
        baseline=read_json(ROOT/'spec/dynamic-windows-legacy-abi.json')
        for v in range(1,7):
            catalog=self.session.catalog_for(v)
            for row in baseline['versions'][str(v)]:
                c=next(c for sig,c in catalog.signatures[row['name']] if c.overload_id==row['contract']['overload_id'])
                self.assertEqual(digest(asdict(c)),digest(row['contract']))
            p='' if v<5 else 'ta.';program=self.session.compile(code(v,f'plot({p}highest(close,2))'))
            from tools.conformance import operations
            self.assertIn('series.highest',operations(program.statements[-1]));self.assertNotIn('window.highest',operations(program.statements[-1]))
    def test_all_versions_changing_windows_match_independent_oracles(self):
        expected=oracle(VALUES,[i%4+1 for i in range(len(VALUES))])
        for v in range(1,7):
            program=decode(encode(self.session.compile(code(v,body(v)))),ProgramIR);result=self.session.execute(program,data=DATA)
            for actual,wanted in zip(result.outputs,expected,strict=True):self.close(actual.values,wanted)
    def test_named_calls_implicit_market_sources_and_udf_state_once(self):
        for v in range(3,7):
            p='' if v<5 else 'ta.';program=self.session.compile(code(v,'n=bar_index%3+1\n'+f'filter(x) => {p}wma(x,n)\nplot({p}highest(length=n,source=filter(close)))\nplot({p}highest(n))'))
            runtime=self.session.validate_program(program);ev=runtime.prepare(program);d=HistoricalDriver(ev,MemorySource(DATA));d.run()
            self.assertEqual(len([s for s in ev.states.values() if isinstance(s,PrefixWindow)]),1)
            self.assertTrue(all(s.calls==len(VALUES) for s in ev.states.values()))
    def test_requested_window_uses_private_prices_and_private_bar_index(self):
        from test_request_expressions import PRIMARY,HIGHER,DATA as PRIMARY_DATA,REQUESTED,mapped
        for v in range(1,7):
            p='' if v<5 else 'ta.';req='security' if v<5 else 'request.security'
            program=self.session.compile(code(v,f'calc(x) => {p}highest(x,bar_index%3+1)\nplot({req}("{HIGHER.symbol}","2",calc(close)))'))
            result=self.session.execute(program,data=PRIMARY_DATA,primary_context=PRIMARY,context_sources=((ContextBinding(HIGHER,'a'*64),MemorySource(REQUESTED)),))
            self.assertEqual(result.outputs[0].values,mapped((11.,14.,14.,19.,19.,22.),v))
    def test_checkpoint_every_version_restores_growing_and_shrinking_windows(self):
        for v in range(1,7):
            program=self.session.compile(code(v,body(v)));runtime=self.session.validate_program(program);expected=self.session.execute(program,data=DATA)
            for cut in (1,3,7,12):
                first=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));saved=[]
                first.run(pause_after=cut,checkpoint=lambda e:saved.append(loads(canonical(e.checkpoint()))))
                restored=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));restored.restore(saved[0]);restored.run()
                self.assertEqual(restored.finish(data_identity(DATA)),expected)
    def test_forged_implicit_input_and_window_identity_are_rejected(self):
        program=self.session.compile(code(6,'n=bar_index%3+1\nplot(ta.highest(n))'))
        for mutate in (lambda n:replace(n,signature_sha256='0'*64),lambda n:replace(n,arguments=(replace(n.arguments[0],expression=replace(n.arguments[0].expression,contract=replace(n.arguments[0].expression.contract,slot='low'))),n.arguments[1]))):
            bad=rewrite(program,lambda n:mutate(n) if n.operation=='window.highest' else n)
            with self.assertRaises(OpenPineError):self.session.validate_program(bad)
    def test_generated_profile_examples_and_corpus_are_current(self):
        self.assertEqual(profile(),read_json(ROOT/'spec/dynamic-windows-profile.json'))
        for root,files_ in [('examples/dynamic-windows',examples()),('conformance/dynamic-windows',corpus())]:
            for name,raw in files_.items():self.assertEqual((ROOT/root/name).read_bytes(),raw)
    def test_requested_private_index_permission_does_not_escape_length_dependencies(self):
        for v in (1,6):
            p='' if v<5 else 'ta.';req='security' if v<5 else 'request.security'
            for expression in ('bar_index',f'{p}highest(bar_index*1.0,bar_index%3+1)',f'{p}highest(close,n)+n'):
                with self.assertRaises(OpenPineError):self.session.compile(code(v,'n=bar_index%3+1\n'+f'plot({req}("TEST:A","2",{expression}))'))
            # A pure length helper may read the private index beneath the length.
            self.session.compile(code(v,f'length(x) => bar_index%3+1\nplot({req}("TEST:A","2",{p}highest(close,length(close))))'))

def json_roundtrip(value):return loads(canonical(value))
if __name__=='__main__':unittest.main()
