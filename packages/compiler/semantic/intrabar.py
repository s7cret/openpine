"""An additive header selected only by the explicit source parameter."""
from dataclasses import replace
from packages.contracts.intrabar import OPERATION,policy
from .specification import value_sha

def install(catalog):
    v=catalog.language_version
    catalog.intrabar_header=None
    if v<2 or not catalog.economics_header:return
    signature,base=catalog.economics_header
    oid=f'overload.openpine.intrabar.v{v}.strategy';pid=f'parameter.{oid}.calc_on_every_tick'
    signature={**signature,'parameters':[*signature['parameters'],{'name':'calc_on_every_tick','parameter_id':pid,
        'requiredness':{'state':'optional'},'default':{'kind':'literal','value':False}}]}
    contract=replace(base,symbol_id='symbol.'+oid,overload_id=oid,assertion_id='assertion.'+oid,
        signature_sha256=value_sha(policy(v)),operation=OPERATION,parameter_ids=(*base.parameter_ids,pid),
        parameter_types=(*base.parameter_types,('bool',)),qualifier_limits=(*base.qualifier_limits,'const'))
    catalog.contracts[oid]=contract;catalog.intrabar_header=(signature,contract)

def header(binder,call):
    if binder.catalog.intrabar_header is None:
        binder.fail('PROFILE_UNCOVERED','INTRABAR_HEADER_VERSION','calc_on_every_tick is not attested for Pine v1; select the explicit OpenPine calculation profile instead.',call.span)
    return binder.catalog.intrabar_header
