"""Select a distinct dynamic contract after binding argument expressions once."""
from copy import deepcopy
from dataclasses import asdict
from packages.contracts.dynamic_windows import BASE_OPERATIONS,contract,admit_profile
from .specification import ROOT,read_json,SpecError

def install(catalog):
    catalog.dynamic_windows={}
    if 'close' not in catalog.references:return
    version=catalog.language_version
    try:admit_profile(ROOT,catalog.snapshot,version)
    except ValueError as error:raise SpecError('SPEC_EVIDENCE',str(error)) from error
    baseline=read_json(ROOT/'spec/dynamic-windows-legacy-abi.json')['versions'][str(version)]
    expected={r['contract']['overload_id']:r for r in baseline};actual=set()
    for name,candidates in catalog.signatures.items():
        for signature,base in candidates:
            if not base or base.operation not in BASE_OPERATIONS|{'series.sma'}:continue
            actual.add(base.overload_id);row=expected.get(base.overload_id)
            item=catalog.technical.get(base.overload_id) or catalog.signals.get(base.overload_id)
            # JSON changes tuple/list representation only; canonical digest checks every ABI field.
            from packages.contracts.wire import digest
            if not row or digest(asdict(base))!=digest(row['contract']) or signature!=row['signature'] or digest(asdict(item[1]) if item else None)!=digest(row['intrinsic']):
                raise SpecError('SPEC_EVIDENCE','Fixed window ABI differs from the reviewed baseline.')
            if base.operation=='series.sma':continue
            selected=contract(base,version);sig=deepcopy(signature)
            for p,pid in zip(sig['parameters'],selected.parameter_ids,strict=True):p['parameter_id']=pid
            catalog.dynamic_windows[base.overload_id]=(sig,selected)
            if item:
                api,intrinsic=item;new_intrinsic=contract(intrinsic,version)
                catalog.contracts[new_intrinsic.overload_id]=new_intrinsic
                target=catalog.technical if base.overload_id in catalog.technical else catalog.signals
                target[selected.overload_id]=({**api,'operation':new_intrinsic.operation},new_intrinsic)
            else:catalog.contracts[selected.overload_id]=selected
    if actual!=expected.keys():raise SpecError('SPEC_EVIDENCE','Window ABI inventory differs.')

def select(catalog,call,expressions,signature,base):
    candidate=catalog.dynamic_windows.get(base.overload_id)
    if not candidate:return signature,base
    position=len(signature['parameters'])-(2 if base.operation in ('series.variance','series.stdev') else 1)
    name=signature['parameters'][position]['name']
    for i,(a,e) in enumerate(zip(call.arguments,expressions,strict=True)):
        if (a.name==name or a.name is None and i==position) and e.value_type.qualifier=='series':return candidate
    return signature,base
