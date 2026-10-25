"""Versioned scalar trade references and numeric indexed trade readers."""
from packages.contracts import CallContract,ReferenceContract,ValueType
from packages.contracts.performance import VARIABLES,OPERATION,apis
from .specification import value_sha

def install(catalog):
 if not catalog.snapshot.performance:return
 v=catalog.language_version;row=catalog.snapshot.performance['versions'][str(v)]
 for name,base in VARIABLES.items():
  ident=f'openpine.performance.v{v}.{name}'
  catalog.references['strategy.'+name]=ReferenceContract('symbol.'+ident,'assertion.'+ident,value_sha(row),'strategy.'+name,ValueType(base,'series'))
 for name,group,field,base in apis(v):
  oid=f'overload.openpine.performance.v{v}.{group}.{field}';pid=f'parameter.{oid}.trade_num'
  signature={'parameters':[{'name':'trade_num','parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}}],'binding':{'named':True},'effects':['reads_trading']}
  contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),OPERATION,(pid,),(('int',),),('series',),None,('reads_trading',),ValueType(base,'series'),'fixed')
  catalog.contracts[oid]=contract;catalog.signatures[name]=[(signature,contract)]
