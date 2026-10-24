"""Explicit lot declaration ABI; legacy nine-argument declarations stay pinned."""
from packages.contracts import CallContract,ValueType
from packages.contracts.lots import lot_apis,lot_policy
from .specification import value_sha,SpecError

def install(catalog):
    catalog.lot_header=None
    if not catalog.snapshot.lots:return
    v=catalog.language_version;row=catalog.snapshot.lots['versions'][str(v)]
    if row['policy']!=lot_policy(v):raise SpecError('IMPLEMENTATION_MISSING','Lot policy differs.')
    for name,operation,parameters in lot_apis(v):
        oid=f'overload.openpine.lots.v{v}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':['declaration']}
        contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),
            tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('declaration',),ValueType('void'),'fixed')
        catalog.contracts[oid]=contract;catalog.lot_header=(signature,contract)
