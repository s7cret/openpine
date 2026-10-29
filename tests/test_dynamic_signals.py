"""Independent signal windows, bounded storage and recovery contracts."""
import copy,math,random,unittest
from dataclasses import asdict,replace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR
from packages.contracts.codec import encode,decode
from packages.contracts.wire import canonical,loads,read_json,digest
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.marketdata import data_identity
from packages.runtime.history import StorageBudget
from packages.runtime.series import KernelError
from packages.runtime.series.dynamic_signals import SignalWindow,export_window,restore_window,fork_window,KINDS
from packages.contracts.dynamic_signals import NAMES,HORIZON
from tools.dynamic_signals_spec import oracle,profile,examples,corpus,body
from tools.specification import ROOT
from test_composite import DATA,VALUES
from test_dynamic_windows import rewrite

def code(v,body_):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Signals")\n'+body_
def args(kind,x,vol,n):return (vol,x,n) if kind=='swindow.vwma' else (x,n)

class DynamicSignalTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
 def close(self,a,b):
  self.assertEqual(len(a),len(b))
  for x,y in zip(a,b,strict=True):
   if y is None:self.assertIsNone(x)
   elif type(y) is bool:self.assertIs(x,y)
   else:self.assertTrue(x is not None and math.isclose(x,y,rel_tol=1e-12,abs_tol=1e-12),(x,y))
 def test_random_changing_lengths_match_direct_lists_all_versions(self):
  rng=random.Random(239);xs=[None if i%9==0 else float(rng.randrange(-3,5)) for i in range(120)];vs=[None if i%11==0 else float(rng.randrange(4)) for i in range(120)];ns=[rng.randrange(1,17) for _ in xs]
  for v in (1,6):
   wanted=oracle(xs,vs,ns,v)
   for k,row in zip(KINDS,wanted,strict=True):
    s=SignalWindow(StorageBudget(),k,v);self.close([s.update(*args(k,x,vol,n)) for x,vol,n in zip(xs,vs,ns)],row)
 def test_all_versions_programs_match_oracles(self):
  for v in range(1,7):
   p=decode(encode(self.session.compile(code(v,body(v)))),ProgramIR);r=self.session.execute(p,data=DATA)
   expected=oracle(VALUES,[1.]*len(VALUES),[i%4+1 for i in range(len(VALUES))],v)
   for i,(actual,wanted) in enumerate(zip(r.outputs,expected,strict=True)):self.close(actual.values,[1 if x else 0 for x in wanted] if i in (3,4) else wanted)
 def test_nine_fixed_abis_and_paths_remain_exact(self):
  base=read_json(ROOT/'spec/dynamic-signals-legacy-abi.json')
  for v in range(1,7):
   c=self.session.catalog_for(v)
   for row in base['versions'][str(v)]:
    old=next(a for _,a in c.signatures[row['name']] if a.overload_id==row['contract']['overload_id']);self.assertEqual(digest(asdict(old)),digest(row['contract']))
   p='' if v<5 else 'ta.'
   from tools.conformance import operations
   for name in NAMES:
    call=f'{p}{name}(close,2)';call+=' ? 1 : 0' if name in ('rising','falling') else ''
    program=self.session.compile(code(v,'plot('+call+')'))
    self.assertIn('signals.'+name,operations(program.statements[-1]))
 def test_horizon_wrap_shrink_then_expand_and_constant_storage(self):
  for kind in KINDS:
   s=SignalWindow(StorageBudget(),kind)
   for i in range(HORIZON+5):s.update(*args(kind,float(i),1.,1))
   before=s.budget.cells;x=float(HORIZON+5);actual=s.update(*args(kind,x,1.,HORIZON))
   wanted={'swindow.vwma':(6+x)/2,'swindow.mom':float(HORIZON),'swindow.roc':float(HORIZON)*100/5,'swindow.rising':True,'swindow.falling':False,'swindow.highestbars':0,'swindow.lowestbars':-(HORIZON-1)}[kind]
   self.assertEqual(actual,wanted);self.assertEqual(s.budget.cells,before)
   k,value=export_window(s);restored=restore_window(k,loads(canonical(value)),StorageBudget(),s.calls,6);self.assertEqual(export_window(restored),export_window(s))
 def test_every_kind_codec_continues_after_na_and_new_lengths(self):
  for v in (1,6):
   for kind in KINDS:
    s=SignalWindow(StorageBudget(),kind,v)
    for x,vol,n in [(3.,1.,1),(None,0.,3),(7.,None,2),(4.,2.,1)]:s.update(*args(kind,x,vol,n))
    k,value=export_window(s);b=StorageBudget();r=restore_window(k,loads(canonical(value)),b,s.calls,v)
    self.assertEqual((export_window(r),b.cells),(export_window(s),s.budget.cells))
    for x,vol,n in [(None,1.,1),(8.,0.,4),(2.,3.,2),(7.,2.,5)]:self.assertEqual(r.update(*args(kind,x,vol,n)),s.update(*args(kind,x,vol,n)))
 def test_malformed_checkpoints_reject_counts_types_pairs_and_prefixes(self):
  for kind in KINDS:
   s=SignalWindow(StorageBudget(),kind);s.update(*args(kind,3.,1.,1));k,original=export_window(s)
   mutations=[lambda v:v.update(calls=True),lambda v:v.update(length=HORIZON+1),lambda v:v.update(count=2),lambda v:v.update(extra=True),lambda v:v['values'].__setitem__(0,False)]
   if kind=='swindow.vwma':mutations.extend([lambda v:v['anchors'].__setitem__(0,1),lambda v:v['totals'].__setitem__(0,0),lambda v:v['values'].__setitem__(0,[3.,-1.])])
   for mutation in mutations:
    bad=copy.deepcopy(original);mutation(bad)
    with self.assertRaises(ValueError):restore_window(k,bad,StorageBudget(),1,6)
 def test_changed_cell_previews_preserve_confirmed_samples(self):
  for kind in KINDS:
   s=SignalWindow(StorageBudget(),kind)
   for i in range(12):s.update(*args(kind,float(i),1.,i%4+1))
   before=export_window(s);clone=fork_window(s,copy.copy(s.budget));clone.update(*args(kind,100.,2.,8))
   self.assertEqual(before,export_window(s))
   if s.nodes:self.assertLessEqual(len(clone.nodes.changed),18);self.assertEqual(len(clone.values.items.changed),1)
   else:self.assertEqual(len(clone.values.changed),1)
 def test_invalid_lengths_and_volume_leave_state_unchanged(self):
  for kind in KINDS:
   s=SignalWindow(StorageBudget(),kind);s.update(*args(kind,3.,1.,1));before=export_window(s)
   for n in (None,True,0,-1,100001,1.0):
    with self.assertRaises(KernelError):s.update(*args(kind,5.,1.,n))
    self.assertEqual(export_window(s),before)
  with self.assertRaises(KernelError):s=SignalWindow(StorageBudget(),'swindow.vwma');s.update(-1.,3.,1)
 def test_storage_preflight_and_counter_bounds(self):
  s=SignalWindow(StorageBudget(limit=1),'swindow.highestbars')
  with self.assertRaises(KernelError):s.update(2.,1)
  self.assertEqual((s.calls,s.count,s.budget.cells,s.nodes),(0,0,0,[]))
  s=SignalWindow(StorageBudget(limit=5),'swindow.vwma')
  with self.assertRaises(KernelError):s.update(1.,2.,1)
  self.assertEqual((s.count,s.calls,s.budget.cells),(0,0,2))
  for kind in KINDS:
   s=SignalWindow(StorageBudget(),kind);s.calls=2**63-1
   with self.assertRaises(KernelError):s.update(*args(kind,1.,1.,1))
 def test_zero_volume_pair_missingness_and_exact_large_products(self):
  s=SignalWindow(StorageBudget(),'swindow.vwma');self.assertIsNone(s.update(0.,4.,1));self.assertIsNone(s.update(None,8.,2))
  self.assertEqual(s.update(2.,6.,2),6.);self.assertEqual(s.update(None,None,1),6.)
  s=SignalWindow(StorageBudget(),'swindow.vwma');self.assertIsNone(s.update(1e308,-1e308,2));self.assertEqual(s.update(1e308,1e308,2),0.)
  k,v=export_window(s);self.assertEqual(export_window(restore_window(k,loads(canonical(v)),StorageBudget(),2,6)),(k,v))
 def test_all_versions_implicit_offsets_and_named_helpers(self):
  for v in range(1,7):
   p='' if v<5 else 'ta.'
   text='n=bar_index%4+1\n'+f'plot({p}highestbars(n))\nplot({p}lowestbars(n))'
   _,r=self.session.run(code(v,text),data=DATA);ns=[i%4+1 for i in range(len(VALUES))]
   for actual,values,k in [(r.outputs[0],[None if x is None else x+2 for x in VALUES],5),(r.outputs[1],[None if x is None else x-2 for x in VALUES],6)]:self.close(actual.values,oracle(values,[1.]*len(values),ns,v)[k])
   if v>=3:
    pgr=self.session.compile(code(v,'n=bar_index%3+1\n'+f'f(x) => {p}vwma(length=n,source=x)\nplot(f(close))'))
    d=HistoricalDriver(self.session.validate_program(pgr).prepare(pgr),MemorySource(DATA));d.run();self.assertTrue(all(s.calls==len(VALUES) for s in d.evaluation.states.values() if isinstance(s,SignalWindow)))
 def test_requested_volume_prices_and_index_belong_to_private_context(self):
  from test_request_expressions import PRIMARY,HIGHER,DATA as PRIMARY_DATA,REQUESTED,mapped
  from packages.contracts.contexts import ContextBinding
  for v in range(1,7):
   p='' if v<5 else 'ta.';request='security' if v<5 else 'request.security'
   for i,name in enumerate(NAMES):
    expr=f'x={request}("{HIGHER.symbol}","2",{p}{name}(close,bar_index%3+1))\nplot(x'+(' ? 1 : 0' if i in (3,4) else '')+')'
    program=self.session.compile(code(v,expr));r=self.session.execute(program,data=PRIMARY_DATA,primary_context=PRIMARY,context_sources=((ContextBinding(HIGHER,'a'*64),MemorySource(REQUESTED)),))
    expected=oracle([b.close for b in REQUESTED.bars],[b.volume for b in REQUESTED.bars],[j%3+1 for j in range(len(REQUESTED.bars))],v)[i];expected=mapped(tuple(expected),v)
    self.close(r.outputs[0].values,[1 if x else 0 for x in expected] if i in (3,4) else expected)
 def test_loops_checkpoint_all_supported_versions(self):
  for v in range(2,7):
   p='' if v<5 else 'ta.';program=self.session.compile(code(v,f'x=for i=0 to 2\n    {p}highestbars(close+i,i+1)\nplot(x)'))
   expected=self.session.execute(program,data=DATA)
   for cut in (2,5,9):
    runtime=self.session.validate_program(program);first=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));saved=[];first.run(pause_after=cut,checkpoint=lambda e:saved.append(loads(canonical(e.checkpoint()))))
    second=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));second.restore(saved[0]);second.run();self.assertEqual(second.finish(data_identity(DATA)),expected)
 def test_forged_volume_default_and_variant_contracts_are_rejected(self):
  program=self.session.compile(code(6,'plot(ta.vwma(close,bar_index%3+1))'))
  for mutate in (lambda n:replace(n,signature_sha256='0'*64),lambda n:replace(n,arguments=(replace(n.arguments[0],expression=replace(n.arguments[0].expression,contract=replace(n.arguments[0].expression.contract,slot='close'))),*n.arguments[1:]))):
   with self.assertRaises(OpenPineError):self.session.validate_program(rewrite(program,lambda n:mutate(n) if n.operation=='swindow.vwma' else n))
 def test_private_bar_index_permission_stays_in_length(self):
  for expression in ('bar_index','ta.vwma(bar_index,bar_index%3+1)','ta.highestbars(bar_index,bar_index%3+1)'):
   # The request-environment overlay extends the earlier length-only boundary.
   self.session.compile(code(6,f'plot(request.security("TEST:A","2",{expression}))'))
 def test_profile_and_generated_files_are_current(self):
  self.assertEqual(profile(),read_json(ROOT/'spec/dynamic-signals-profile.json'))
  for root,files in [('examples/dynamic-signals',examples()),('conformance/dynamic-signals',corpus())]:
   for n,b in files.items():self.assertEqual((ROOT/root/n).read_bytes(),b)

if __name__=='__main__':unittest.main()
