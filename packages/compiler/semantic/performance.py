"""Versioned scalar trade references and numeric indexed trade readers."""
from packages.contracts import CallContract,ReferenceContract,ValueType
from packages.contracts.performance import VARIABLES,OPERATION,RETURN_FIELDS,apis
from .specification import value_sha

def install(catalog):
 if not catalog.snapshot.performance:return
 v=catalog.language_version;row=catalog.snapshot.performance['versions'][str(v)]
 for name,base in VARIABLES.items():
  ident=f'openpine.performance.v{v}.{name}'
  catalog.references['strategy.'+name]=ReferenceContract('symbol.'+ident,'assertion.'+ident,value_sha(row),'strategy.'+name,ValueType(base,'series'))
 from packages.contracts.trade_identity import READERS
 additional=[]
 references={r['name']:r for r in catalog.snapshot.references[v]['entries'] if r['category']=='functions' and r['name'].startswith(('strategy.opentrades.','strategy.closedtrades.'))}
 if v>=5:
  for group,fields in READERS.items():
   for field in (*fields,*RETURN_FIELDS):
    name=f'strategy.{group}.{field}'
    evidence=references[name]
    additional.append((name,group,field,'string' if field in fields else 'float',evidence['source_record_sha256']))
 base_sha=value_sha(row)
 for name,group,field,base,evidence_sha in [*((*api,base_sha) for api in apis(v)),*additional]:
  oid=f'overload.openpine.performance.v{v}.{group}.{field}';pid=f'parameter.{oid}.trade_num'
  signature={'parameters':[{'name':'trade_num','parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}}],'binding':{'named':True},'effects':['reads_trading']}
  contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,evidence_sha,OPERATION,(pid,),(('int',),),('series',),None,('reads_trading',),ValueType(base,'series'),'fixed')
  catalog.contracts[oid]=contract;catalog.signatures[name]=[(signature,contract)]
