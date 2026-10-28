"""Version evidence and independent direct-list changing-signal expectations."""
import argparse,math
from dataclasses import asdict
from fractions import Fraction
from tools.specification import ROOT
from tools.primary_targets_spec import examples as primary_examples,source as primary_source
from packages.contracts import HistoricalData,HistoricalBar
from packages.contracts.wire import read_json,canonical,digest
from packages.contracts.dynamic_signals import policy,NAMES

def profile():
 lock=read_json(ROOT/'spec/lock.json');e=read_json(ROOT/'spec/dynamic-signals-evidence.json');versions={}
 for v in range(1,7):
  refs=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json');known={r['entry_id']:r['source_record_sha256'] for r in refs['entries']};rows=e['versions'][str(v)]['entries']
  assert all(known[r['entry_id']]==r['source_record_sha256'] for r in rows)
  for name in NAMES:
   candidates=[r for r in rows if r['name'].removeprefix('ta.')==name];assert candidates
   if v>=4:assert any(any('series int' in t or 'series[integer]' in t for t in p['types']) for r in candidates for p in r['parameters'] if p['name']=='length')
  versions[str(v)]={'policy':policy(v),'reference_sha256':digest(refs)}
 return {'schema_version':'openpine.dynamic-signals-profile.1','source_lock_sha256':digest(lock),'versions':versions,
 'evidence_sha256':digest(e),'legacy_abi_sha256':digest(read_json(ROOT/'spec/dynamic-signals-legacy-abi.json')),
 'decisions':['v4 attests series[integer], v5/v6 series int. v1-v3 qualifier gaps are an explicit reconstruction.',
 'Preserve all nine fixed signal public/intrinsic contracts per version. Only a series length selects the new ABI.',
 'Short offset overloads inherit the existing reviewed profile, including historical reconstructions; missing Wiki overload evidence is not fabricated.',
 'VWMA retains defined pairs; MOM/ROC retain reached calls including NA; trend compares current to previous defined samples; offsets use physical reached-call positions and latest ties.',
 'Private bar_index is admitted only beneath length expressions, including implicit-volume VWMA argument 2. Existing source/standalone restrictions remain.',
 'Exact TradingView comparison is deferred.']}

def oracle(values,volumes,lengths,version=6):
 reached=[];defined=[];pairs=[];out=[[] for _ in NAMES]
 for x,vol,n in zip(values,volumes,lengths,strict=True):
  previous=reached[-n] if len(reached)>=n else None
  prior=defined[-n:]
  missing=False if version==6 else None
  rising=(x>max(prior)) if x is not None and len(prior)==n else missing
  falling=(x<min(prior)) if x is not None and len(prior)==n else missing
  if x is not None:defined.append(x)
  if x is not None and vol is not None:pairs.append((Fraction(x),Fraction(vol)))
  window=pairs[-n:];den=sum(v for _,v in window)
  vwma=float(sum(a*v for a,v in window)/den) if len(window)==n and den else None
  mom=float(Fraction(x)-Fraction(previous)) if x is not None and previous is not None else None
  roc=float((Fraction(x)-Fraction(previous))*100/Fraction(previous)) if mom is not None and previous else None
  reached.append(x);physical=[(a,i) for i,a in enumerate(reached[-n:]) if a is not None]
  high=max(physical,key=lambda z:(z[0],z[1]))[1]-len(reached[-n:])+1 if physical else None
  low=min(physical,key=lambda z:(z[0],-z[1]))[1]-len(reached[-n:])+1 if physical else None
  for a,y in zip(out,(vwma,mom,roc,rising,falling,high,low),strict=True):a.append(y)
 return out

def source(v,mode='inputs',*,lower=False):
 p='' if v<5 else 'ta.'
 text=primary_examples()[f'v{v}-lower.pine'].decode() if lower else primary_source(v,mode)
 return text.replace('Primary contexts','Signal windows').replace(',close)',f',close+{p}highestbars(close,bar_index%3+1))')

def body(v,x='close'):
 p='' if v<5 else 'ta.'
 return 'n=bar_index%4+1\n'+''.join(f'plot({p}{name}({x},n)'+(' ? 1 : 0' if name in ('rising','falling') else '')+')\n' for name in NAMES)

def examples():
 files={n:b for n,b in primary_examples().items() if n.endswith('.json')}
 for v in range(1,7):
  for mode in ('references','inputs'):files[f'v{v}-{mode}.pine']=source(v,mode).encode()
  p='' if v<5 else 'ta.';header=f'//@version={v}\n'+('study' if v<5 else 'indicator')
  files[f'v{v}-adaptive.pine']=(header+'("Signal windows")\n'+body(v)).encode()
  for family,names in [('average',('vwma',)),('momentum',('mom','roc')),('trend',('rising','falling')),('offsets',('highestbars','lowestbars'))]:
   text=header+'("Adaptive '+family+'")\nn=bar_index%4+1\n'
   text+=''.join(f'plot({p}{name}(close,n)'+(' ? 1 : 0' if name in ('rising','falling') else '')+')\n' for name in names)
   files[f'v{v}-{family}.pine']=text.encode()
  files[f'v{v}-since-signal.pine']=(header+'("Signal age")\nn='+('max' if v<5 else 'math.max')+f'(1,nz({p}barssince(close>open))+1)\nplot({p}highestbars(high,n))\nplot({p}mom(close,n))\n').encode()
 for v in (5,6):files[f'v{v}-lower.pine']=source(v,lower=True).encode()
 return files

def corpus():
 values=[4.,7.,None,6.,7.,0.,7.,4.];volumes=[1.,0.,2.,None,3.,1.,2.,0.];lengths=[i%4+1 for i in range(8)]
 files={};cases=[]
 for key,vs in [('bars',volumes),('zero-volume',[0.]*8)]:
  data=HistoricalData('signal-windows','TEST:WINDOWS','1',tuple(HistoricalBar(i*60000,x,x,None if x is None else x+2,None if x is None else x-2,vs[i]) for i,x in enumerate(values)),schema_version='openpine.historical-bars.2')
  files[key+'.json']=canonical(asdict(data))+b'\n'
 for v in range(1,7):
  p='' if v<5 else 'ta.';header=f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Signal windows")\n'
  def add(key,text,outputs=None,reject=None,bars='bars.json',types=None):
   name=f'v{v}-{key}.pine';files[name]=(header+text).encode()
   if outputs is not None and types is None:outputs=[[(1 if x is True else 0) for x in row] if i in (3,4) else row for i,row in enumerate(outputs)]
   expected={'status':'rejected','category':reject[0],'code':reject[1]} if reject else {'status':'executed','title':'Signal windows','shorttitle':'','overlay':False,'observation_count':8,'outputs':[{'title':'','type':t,'values':row} for t,row in zip(types or ['float']*3+['int']*4,outputs,strict=True)],'qualifiers':['series']*len(outputs)}
   cases.append({'case_id':f'v{v}-{key}','language_version':v,'source':name,'bars':bars,'expected':expected,'numeric_tolerance':{'absolute':1e-12,'relative':1e-12}})
  add('changing',body(v),oracle(values,volumes,lengths,v))
  add('zero-volume',body(v),oracle(values,[0.]*8,lengths,v),bars='zero-volume.json')
  highs=[None if x is None else x+2 for x in values];lows=[None if x is None else x-2 for x in values]
  add('implicit','n=bar_index%4+1\n'+f'plot({p}highestbars(n))\nplot({p}lowestbars(n))\n',[oracle(highs,volumes,lengths,v)[5],oracle(lows,volumes,lengths,v)[6]],types=['int','int'])
  for key,n in [('zero','0'),('negative','-1'),('missing','int(na)'),('horizon','100001')]:add('reject-'+key,'n=bar_index==2 ? '+n+f' : 2\nplot({p}mom(close,n))\n',reject=('RUNTIME_ERROR','WINDOW_LENGTH'))
  add('reject-target','n=bar_index%3+1\nplot('+('security' if v<5 else 'request.security')+'("TEST:A",'+('tostring' if v<5 else 'str.tostring')+f'({p}mom(close,n)),close))\n',reject=('LANGUAGE_INVALID','ARGUMENT_QUALIFIER'))
 files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','oracle':{'kind':'independent_manual_expectations','basis':'Direct lists, Fraction pair weights and manual reached-call positions; no runtime kernel produces expectations.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
 return files

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
 files={'spec/dynamic-signals-profile.json':canonical(profile())+b'\n'}
 files.update({'examples/dynamic-signals/'+n:b for n,b in examples().items()});files.update({'conformance/dynamic-signals/'+n:b for n,b in corpus().items()})
 for name,raw in files.items():
  path=ROOT/name
  if a.check:assert path.read_bytes()==raw,name
  else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
 print('Changing signals v1-v6, v4-v6 series evidence and independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
