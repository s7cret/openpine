"""Install the closed, separately pinned composite-analysis contracts."""
from packages.contracts import CallContract,ValueType,TupleType
from packages.contracts.composite import composite_policy,composite_apis
from .specification import value_sha,SpecError


def install(catalog):
    release=getattr(catalog.snapshot,'composite',None)
    if not release or 'close' not in catalog.references:return
    version=catalog.language_version;row=release['versions'][str(version)]
    if row['policy']!=composite_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Composite policy differs.')
    for api in row['apis']:
        oid='overload.'+api['id'];params=api['parameters']
        types=tuple(ValueType(base,'series') for base in api['result'])
        result=types[0] if len(types)==1 else TupleType(types)
        contract=CallContract('symbol.'+api['id'],oid,'assertion.'+api['id'],value_sha(api),api['operation'],
            tuple('parameter.'+oid+'.'+p['name'] for p in params),tuple(tuple(p['types']) for p in params),
            tuple(p['qualifier'] for p in params),None,tuple(api['effects']),result,'fixed_series')
        signature={'parameters':[{'name':p['name'],'parameter_id':pid,
            'requiredness':{'state':'required' if p['default'] is None else 'optional'},
            'default':{'kind':'none'} if p['default'] is None else {'kind':'literal','value':p['default']}}
            for p,pid in zip(params,contract.parameter_ids,strict=True)],
            'binding':{'named':api['named']},'effects':api['effects']}
        catalog.contracts[oid]=contract
        catalog.signatures[api['name']]=[(signature,contract)]
