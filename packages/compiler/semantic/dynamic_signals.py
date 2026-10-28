"""Choose changing signals after the one shared argument-binding pass."""
from copy import deepcopy
from dataclasses import asdict
from packages.contracts.dynamic_signals import BASE_OPERATIONS,contract,admit_profile
from packages.contracts.wire import digest
from .specification import ROOT,read_json,SpecError

def install(catalog):
 catalog.dynamic_signals={}
 if 'close' not in catalog.references:return
 v=catalog.language_version
 try:admit_profile(ROOT,catalog.snapshot,v)
 except ValueError as e:raise SpecError('SPEC_EVIDENCE',str(e)) from e
 expected={r['contract']['overload_id']:r for r in read_json(ROOT/'spec/dynamic-signals-legacy-abi.json')['versions'][str(v)]};actual=set()
 for name,candidates in catalog.signatures.items():
  for sig,base in candidates:
   if not base or base.operation not in BASE_OPERATIONS:continue
   actual.add(base.overload_id);row=expected.get(base.overload_id);item=catalog.signals.get(base.overload_id)
   if not row or not item or digest(asdict(base))!=digest(row['contract']) or sig!=row['signature'] or digest(asdict(item[1]))!=digest(row['intrinsic']):raise SpecError('SPEC_EVIDENCE','Fixed signal ABI differs.')
   new=contract(base,v);signature=deepcopy(sig)
   for p,pid in zip(signature['parameters'],new.parameter_ids,strict=True):p['parameter_id']=pid
   catalog.dynamic_signals[base.overload_id]=(signature,new)
   api,intrinsic=item;intrinsic=contract(intrinsic,v);catalog.contracts[intrinsic.overload_id]=intrinsic
   catalog.signals[new.overload_id]=({**api,'operation':intrinsic.operation},intrinsic)
 if actual!=expected.keys():raise SpecError('SPEC_EVIDENCE','Signal ABI inventory differs.')

def select(catalog,call,expressions,signature,base):
 candidate=catalog.dynamic_signals.get(base.overload_id)
 if not candidate:return signature,base
 position=len(signature['parameters'])-1;name=signature['parameters'][position]['name']
 for i,(a,e) in enumerate(zip(call.arguments,expressions,strict=True)):
  if (a.name==name or a.name is None and i==position) and e.value_type.qualifier=='series':return candidate
 return signature,base
