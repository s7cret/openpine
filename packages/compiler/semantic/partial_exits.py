"""Register opt-in partial signatures without overloading legacy defaults."""
from packages.contracts import CallContract,ValueType
from packages.contracts.partial_exits import partial_apis,partial_policy
from .specification import value_sha,SpecError

def install(catalog):
    catalog.partial_exits={}
    if not catalog.snapshot.partial_exits:return
    version=catalog.language_version;row=catalog.snapshot.partial_exits['versions'][str(version)]
    if row['policy']!=partial_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Partial exit policy differs.')
    for name,operation,parameters in partial_apis(version):
        oid=f'overload.openpine.partial-exits.v{version}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else d if type(d) is dict else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':['emits_order']}
        contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),
            tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('emits_order',),ValueType('void'),'fixed')
        catalog.contracts[oid]=contract;catalog.partial_exits[name]=(signature,contract)
