"""Add the attested v5/v6 flag without changing older strategy signatures."""
from dataclasses import replace
from packages.contracts.magnifier import OPERATION,policy
from .specification import value_sha

def install(catalog):
    catalog.magnifier_header=None
    if not catalog.cycle_header:return
    signature,base=catalog.cycle_header;v=catalog.language_version
    oid=f'overload.openpine.magnifier.v{v}.strategy';pid=f'parameter.{oid}.use_bar_magnifier'
    signature={**signature,'parameters':[*signature['parameters'],{'name':'use_bar_magnifier','parameter_id':pid,
        'requiredness':{'state':'optional'},'default':{'kind':'literal','value':False}}]}
    contract=replace(base,symbol_id='symbol.'+oid,overload_id=oid,assertion_id='assertion.'+oid,signature_sha256=value_sha(policy(v)),
        operation=OPERATION,parameter_ids=(*base.parameter_ids,pid),parameter_types=(*base.parameter_types,('bool',)),
        qualifier_limits=(*base.qualifier_limits,'const'))
    catalog.contracts[oid]=contract;catalog.magnifier_header=(signature,contract)

def header(binder,call):
    from .strategy_cycle import header as cycle_header
    cycle_header(binder,call)
    if binder.catalog.language_version<5:binder.fail('PROFILE_UNCOVERED','MAGNIFIER_HEADER_VERSION',
        'use_bar_magnifier is attested only for Pine v5/v6; use explicit OpenPine reconstruction settings.',call.span)
    return binder.catalog.magnifier_header
