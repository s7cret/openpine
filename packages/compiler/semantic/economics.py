"""Select the economic header only for explicit new modes/fee kinds."""
from packages.contracts import CallContract,ValueType
from packages.contracts.economics import economics_policy,economics_apis
from .specification import value_sha,SpecError
def install(catalog):
 catalog.economics_header=None
 if not catalog.snapshot.economics:return
 v=catalog.language_version;row=catalog.snapshot.economics['versions'][str(v)]
 if row['policy']!=economics_policy(v):raise SpecError('IMPLEMENTATION_MISSING','Economic policy differs.')
 catalog.constants.update({'strategy.cash':'cash','strategy.percent_of_equity':'percent_of_equity',
  'strategy.commission.cash_per_contract':'cash_per_contract','strategy.commission.cash_per_order':'cash_per_order'})
 for name,operation,parameters in economics_apis(v):
  oid=f'overload.openpine.economics.v{v}.{name}'
  params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
    'default':{'kind':'none'} if d is None else {'kind':'literal','value':d}} for n,_,_,d in parameters]
  signature={'parameters':params,'binding':{'named':True},'effects':['declaration']}
  c=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('declaration',),ValueType('void'),'fixed')
  catalog.contracts[oid]=c;catalog.economics_header=(signature,c)
