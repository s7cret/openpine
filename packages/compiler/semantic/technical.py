"""Bind public TA signatures and lower implicit market inputs into explicit IR."""
from dataclasses import replace
from packages.contracts import CallContract, ValueType
from packages.contracts.technical import technical_policy, technical_apis
from packages.compiler.frontend import parser
from .specification import SpecError, value_sha


def install(catalog):
    catalog.technical = {}
    if not catalog.snapshot.technical or 'close' not in catalog.references:return
    row = catalog.snapshot.technical['versions'][str(catalog.language_version)]
    if row['policy'] != technical_policy(catalog.language_version):raise SpecError('IMPLEMENTATION_MISSING','Technical policy differs.')
    for api in row['apis']:
        oid='overload.'+api['id']
        public=api['parameters']
        implicit=[{'name':name,'types':['bool' if name=='false' else 'float'],'qualifier':'const' if name=='false' else 'series'} for name in api['implicit']]
        all_params=implicit+public
        def contract(params, public=False):
            identity=oid+('.public' if public else '')
            return CallContract('symbol.'+api['id'],identity,'assertion.'+api['id'],value_sha(api),api['operation'],
                tuple('parameter.'+identity+'.'+p['name'] for p in params),tuple(tuple(p['types']) for p in params),
                tuple(p['qualifier'] for p in params),None,tuple(api['effects']),ValueType('float','series'),'fixed_series')
        intrinsic=contract(all_params)
        catalog.contracts[oid]=intrinsic
        public_contract=contract(public,True)
        signature={'parameters':[{'name':p['name'],'parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}}
            for p,pid in zip(public,public_contract.parameter_ids,strict=True)],'binding':{'named':api['named']},'effects':api['effects']}
        catalog.technical[public_contract.overload_id]=(api,intrinsic)
        if api['reference']:
            catalog.technical['reference:'+api['name']]=(signature,public_contract)
        else:
            if api['name'] not in catalog.technical:
                catalog.signatures[api['name']]=[];catalog.technical[api['name']]=True
            catalog.signatures[api['name']].append((signature,public_contract))


def lower(binder, bound):
    from .binder import BoundArgument, BoundCall
    item=binder.catalog.technical.get(bound.contract.overload_id)
    if item is None:return bound
    api,contract=item
    implicit=[]
    for name in api['implicit']:
        if name=='previous_close':node=parser.History(parser.Reference('close',bound.span),parser.Literal(1,bound.span),bound.span)
        elif name=='false':node=parser.Literal(False,bound.span)
        else:node=parser.Reference(name,bound.span)
        implicit.append(binder.expression(node))
    arguments=tuple(BoundArgument(pid,value,'default',None) for pid,value in zip(contract.parameter_ids,implicit))
    arguments+=tuple(replace(a,parameter_id=contract.parameter_ids[len(implicit)+i]) for i,a in enumerate(bound.arguments))
    order=tuple(len(implicit)+i for i in bound.evaluation_order)+tuple(range(len(implicit)))
    return BoundCall(contract,arguments,order,bound.value_type,bound.span,bound.namespace)


def reference(binder,node):
    selected=binder.catalog.technical.get('reference:'+node.name)
    if selected is None:return None
    signature,contract=selected
    return lower(binder,binder.call(parser.Call(node.name,(),node.span),(),signature,contract,False))
