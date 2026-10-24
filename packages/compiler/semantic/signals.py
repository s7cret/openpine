"""Install reviewed signatures and expose physical VWMA/extremum inputs."""
from dataclasses import replace
from packages.contracts import CallContract,ValueType
from packages.contracts.signals import signal_policy
from packages.compiler.frontend import parser
from .specification import SpecError,value_sha


def install(catalog):
    catalog.signals={}
    if not catalog.snapshot.signals or 'close' not in catalog.references:return
    row=catalog.snapshot.signals['versions'][str(catalog.language_version)]
    if row['policy']!=signal_policy(catalog.language_version):raise SpecError('IMPLEMENTATION_MISSING','Signal policy differs.')
    names=set()
    for api in row['apis']:
        oid='overload.'+api['id'];public=api['parameters']
        implicit=[{'name':n,'types':['float'],'qualifier':'series'} for n in api['implicit']]
        def contract(params,exposed=False):
            identity=oid+('.public' if exposed else '')
            return CallContract('symbol.'+api['id'],identity,'assertion.'+api['id'],value_sha(api),api['operation'],
                tuple('parameter.'+identity+'.'+p['name'] for p in params),tuple(tuple(p['types']) for p in params),tuple(p['qualifier'] for p in params),
                1 if api['result_rule']=='parameter_series' else None,tuple(api['effects']),ValueType(api['result'],'series'),api['result_rule'])
        intrinsic=contract(implicit+public);exposed=contract(public,True)
        catalog.contracts[oid]=intrinsic
        signature={'parameters':[{'name':p['name'],'parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}} for p,pid in zip(public,exposed.parameter_ids,strict=True)],'binding':{'named':api['named']},'effects':api['effects']}
        if api['name'] not in names:catalog.signatures[api['name']]=[];names.add(api['name'])
        catalog.signatures[api['name']].append((signature,exposed));catalog.signals[exposed.overload_id]=(api,intrinsic)


def lower(binder,bound):
    from .binder import BoundArgument,BoundCall
    item=binder.catalog.signals.get(bound.contract.overload_id)
    if item is None:return bound
    api,contract=item
    implicit=[binder.expression(parser.Reference(name,bound.span)) for name in api['implicit']]
    args=tuple(BoundArgument(pid,value,'default',None) for pid,value in zip(contract.parameter_ids,implicit))
    args+=tuple(replace(a,parameter_id=contract.parameter_ids[len(implicit)+i]) for i,a in enumerate(bound.arguments))
    return BoundCall(contract,args,tuple(len(implicit)+i for i in bound.evaluation_order)+tuple(range(len(implicit))),bound.value_type,bound.span,bound.namespace)
