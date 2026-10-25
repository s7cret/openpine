"""Install static global risk declarations with separately pinned contracts."""
from packages.contracts import CallContract,ValueType
from packages.contracts.risk import risk_apis
from .specification import value_sha
def install(catalog):
 if not catalog.snapshot.risk:return
 v=catalog.language_version;row=catalog.snapshot.risk['versions'][str(v)]
 catalog.constants.update({'strategy.direction.'+k:k for k in ('all','long','short')})
 for name,operation,parameters in risk_apis(v):
  oid=f'overload.openpine.risk.v{v}.{name}'
  params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},'default':{'kind':'none'} if d is None else {'kind':'literal','value':d}} for n,_,_,d in parameters]
  signature={'parameters':params,'binding':{'named':True},'effects':['configures_risk']}
  c=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,tuple(p['parameter_id'] for p in params),tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),None,('configures_risk',),ValueType('void'),'fixed')
  catalog.contracts[oid]=c;catalog.signatures[name]=[(signature,c)]
