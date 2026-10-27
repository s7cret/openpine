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
    from .requests import register_prepared
    register_prepared(catalog,contract)
