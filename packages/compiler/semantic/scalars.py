"""Install the pinned closed scalar registry; bind default timeframe explicitly."""
from packages.contracts.scalar_functions import apis,contracts
from packages.contracts.wire import read_json,digest
from .specification import ROOT,SpecError

def install(catalog):
    if 'close' not in catalog.references:return
    from packages.contracts.scalar_targets import admit_profile
    try:admit_profile(ROOT,catalog.snapshot,catalog.language_version)
    except ValueError as error:raise SpecError('SPEC_EVIDENCE',str(error)) from error
    version=catalog.language_version;registered=contracts(version);names=set()
    for a in apis(version):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        parameters=[]
        for i,(p,pid) in enumerate(zip(a['parameters'],c.parameter_ids,strict=True)):
            optional=i>=len(a['parameters'])-len(a['defaults'])
            parameters.append({'name':p['name'],'parameter_id':pid,'requiredness':{'state':'optional' if optional else 'required'},
                'default':{'kind':'literal','value':a['defaults'][i-len(a['parameters'])+len(a['defaults'])]} if optional else {'kind':'none'}})
        if a['name'] not in names:catalog.signatures[a['name']]=[];names.add(a['name'])
        catalog.signatures[a['name']].append(({'parameters':parameters,'binding':{'named':a['named']},'effects':[]},c))

def lower(binder,bound):
    if bound.contract.operation!='scalar.in_seconds' or bound.arguments[0].origin!='default':return bound
    from dataclasses import replace
    from packages.compiler.frontend import parser
    value=binder.expression(parser.Reference('timeframe.period',bound.span))
    return replace(bound,arguments=(replace(bound.arguments[0],expression=value),))
