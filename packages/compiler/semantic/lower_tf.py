"""Bind the bounded v5/v6 API against immutable catalog evidence."""
from packages.contracts import CallContract,ValueType
from packages.contracts.lower_tf import OPERATION,PARAMETERS,DEFAULT_COUNT,policy
from .specification import value_sha,SpecError

def install(catalog):
    v=catalog.language_version
    if v<5 or 'close' not in catalog.references:return
    evidence=[e for e in catalog.snapshot.references[v]['entries'] if e['name']=='request.security_lower_tf']
    if not evidence:raise SpecError('SPEC_EVIDENCE','Missing lower-timeframe catalog evidence.')
    if any([p['name'] for p in e['parameters']]!=list(PARAMETERS) for e in evidence):raise SpecError('SPEC_EVIDENCE','Lower-timeframe parameter evidence differs.')
    oid=f'overload.openpine.lower-tf.v{v}.security';defaults=(None,None,None,False,'',False,DEFAULT_COUNT)
    params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}',
        'requiredness':{'state':'required' if i<3 else 'optional'},
        'default':{'kind':'none'} if i<3 else {'kind':'literal','value':defaults[i]}} for i,n in enumerate(PARAMETERS)]
    signature={'parameters':params,'binding':{'named':True},'effects':['requests_context','reads_history']}
    contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha({'policy':policy(v),
        'evidence':[e['source_record_sha256'] for e in evidence]}),OPERATION,tuple(p['parameter_id'] for p in params),
        (('string',),('string',),('int','float','bool','tuple'),('bool',),('string',),('bool',),('int',)),
        ('const','const','series','const','const','const','const'),None,
        ('requests_context','reads_history'),ValueType('array.float','series'),'lower_tf_arrays')
    catalog.contracts[oid]=contract;catalog.signatures['request.security_lower_tf']=[(signature,contract)]
    from dataclasses import replace
    extended = replace(contract,overload_id=oid+".optional_symbol",
        assertion_id=contract.assertion_id+".optional_symbol",
        signature_sha256=value_sha({"base":contract.signature_sha256,"unknown_symbol":"na_array_reference_per_component.1"}))
    catalog.contracts[extended.overload_id] = extended
    from .requests import register_prepared, register_optional_targets
    register_optional_targets(catalog,extended)
    fx = replace(extended, overload_id=extended.overload_id+".fx",
        assertion_id=extended.assertion_id+".fx",
        signature_sha256=value_sha({"base":extended.signature_sha256,"fx_profile":1}))
    catalog.contracts[fx.overload_id] = fx
    register_optional_targets(catalog,fx)
    register_prepared(catalog,contract)


def normalize_call(binder,call):
    from dataclasses import replace
    from .requests import normalize_currency
    for i,arg in enumerate(call.arguments):
        if arg.name=="currency" or arg.name is None and i==4:
            currency=normalize_currency(binder,arg.expression,arg.span)
            arguments=list(call.arguments)
            arguments[i]=replace(arg,expression=currency)
            return replace(call,arguments=tuple(arguments)),bool(currency.value)
    return call,False
