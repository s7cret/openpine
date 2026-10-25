"""The reviewed extended-exit ABI is selected by explicit relative/trailing names."""
from packages.contracts import CallContract,ValueType
from packages.contracts.trailing import trailing_apis,trailing_policy
from .specification import value_sha,SpecError

def install(catalog):
    catalog.trailing={}
    if not catalog.snapshot.trailing:return
    v=catalog.language_version;row=catalog.snapshot.trailing['versions'][str(v)]
    if row['policy']!=trailing_policy(v):raise SpecError('IMPLEMENTATION_MISSING','Trailing policy differs.')
    for name,operation,parameters in trailing_apis(v):
        oid=f'overload.openpine.trailing.v{v}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else d if type(d) is dict else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':['emits_order']}
        c=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('emits_order',),ValueType('void'),'fixed')
        catalog.contracts[oid]=c;catalog.trailing[name]=(signature,c)
