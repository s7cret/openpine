"""Explicit price arguments select the separately pinned entry-order ABI."""
from packages.contracts import CallContract,ValueType
from packages.contracts.pending_entries import entry_apis,entry_policy
from .specification import value_sha,SpecError

def install(catalog):
    catalog.pending_entries={}
    if not catalog.snapshot.pending_entries:return
    version=catalog.language_version;row=catalog.snapshot.pending_entries['versions'][str(version)]
    if row['policy']!=entry_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Pending entry policy differs.')
    for name,operation,parameters in entry_apis(version):
        oid=f'overload.openpine.pending-entries.v{version}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else d if type(d) is dict else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':['emits_order']}
        contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),
            tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('emits_order',),ValueType('void'),'fixed')
        catalog.contracts[oid]=contract;catalog.pending_entries[name]=(signature,contract)
