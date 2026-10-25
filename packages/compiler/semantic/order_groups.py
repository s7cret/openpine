"""Named-only OCA extensions share one separately pinned call ABI."""
from packages.contracts import CallContract,ValueType
from packages.contracts.order_groups import group_apis,group_policy
from .specification import value_sha,SpecError


def install(catalog):
    catalog.order_groups={}
    if not catalog.snapshot.order_groups:return
    v=catalog.language_version;row=catalog.snapshot.order_groups['versions'][str(v)]
    if row['policy']!=group_policy(v):raise SpecError('IMPLEMENTATION_MISSING','Grouped order policy differs.')
    catalog.constants.update({'strategy.oca.'+kind:kind for kind in ('none','cancel','reduce')})
    for name,operation,parameters in group_apis(v):
        oid=f'overload.openpine.order-groups.v{v}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else d if type(d) is dict else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':['emits_order']}
        contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),
            tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('emits_order',),ValueType('void'),'fixed')
        catalog.contracts[oid]=contract;catalog.order_groups[name]=(signature,contract)
        if name in ('strategy.order','strategy.close_all'):catalog.signatures[name]=[(signature,contract)]
