"""Version-gated source flags use the common economic strategy header."""
from dataclasses import replace
from packages.contracts.strategy_cycle import FLAGS,OPERATION,policy
from .specification import value_sha

def install(catalog):
    catalog.cycle_header=None
    if not catalog.economics_header:return
    signature,base=catalog.economics_header;v=catalog.language_version
    oid=f'overload.openpine.cycle.v{v}.strategy'
    ids=tuple(f'parameter.{oid}.{name}' for name in FLAGS)
    signature={**signature,'parameters':[*signature['parameters'],*[{'name':name,'parameter_id':pid,'requiredness':{'state':'optional'},
        'default':{'kind':'literal','value':False}} for name,pid in zip(FLAGS,ids,strict=True)]]}
    contract=replace(base,symbol_id='symbol.'+oid,overload_id=oid,assertion_id='assertion.'+oid,signature_sha256=value_sha(policy(v)),
        operation=OPERATION,parameter_ids=(*base.parameter_ids,*ids),parameter_types=(*base.parameter_types,*(('bool',),)*3),
        qualifier_limits=(*base.qualifier_limits,*('const',)*3))
    catalog.contracts[oid]=contract;catalog.cycle_header=(signature,contract)

def header(binder,call):
    allowed=policy(binder.catalog.language_version)['source_flags']
    for a in call.arguments:
        if a.name in allowed and not allowed[a.name]:binder.fail('PROFILE_UNCOVERED','CYCLE_HEADER_VERSION',a.name+' is not attested for this Pine version; use explicit OpenPine cycle settings.',call.span)
    if binder.catalog.cycle_header is None:binder.fail('PROFILE_UNCOVERED','CYCLE_HEADER_VERSION','Strategy cycle header is unavailable.',call.span)
    return binder.catalog.cycle_header
