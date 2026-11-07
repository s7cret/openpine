"""Install the additive color ABI without changing historical plot calls."""
from packages.contracts.colors import apis,contracts,constants,admit
from packages.contracts.visual_panes import ABI_SUFFIX
from .specification import ROOT,SpecError

def signature(a,c):
    params=[];start=len(a['parameters'])-len(a['defaults'])
    for i,(p,pid) in enumerate(zip(a['parameters'],c.parameter_ids,strict=True)):
        optional=i>=start
        default = {'kind': 'none'}
        if optional:
            value = a['defaults'][i-start]
            if a['operation'] == 'chart.point.now':
                default = {'kind': 'reference', 'name': value['reference']}
            else:
                default = {'kind': 'na'} if (a['operation'].startswith(('table.', 'polyline.')) or p['name']=='show_last') and value is None else {'kind': 'literal', 'value': value}
        params.append({'name':p['name'],'parameter_id':pid,'requiredness':{'state':'optional' if optional else 'required'},
            'default':default})
    return {'parameters':params,'binding':{'named':a['named']},'effects':a['effects']}

def install(catalog):
    if 'close' not in catalog.references:return
    v=catalog.language_version
    try:admit(ROOT,catalog.snapshot,v)
    except ValueError as e:raise SpecError('SPEC_EVIDENCE',str(e)) from e
    registered=contracts(v)
    catalog.color_constants=constants(v)
    for a in apis(v):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        if a['name']=='na':catalog.color_na=(signature(a,c),c)
        elif a['name']=='plot':
            if a['id'].endswith(ABI_SUFFIX):catalog.color_plot_overlay=(signature(a,c),c)
            else:catalog.color_plot=(signature(a,c),c)
        else:catalog.signatures[a['name']]=[(signature(a,c),c)]

def styled(call):
    return call.name=='plot' and (len(call.arguments)>2 or any(a.name not in (None,'series','title') for a in call.arguments))

def na_call(binder,call,argument):
    return binder.call(call,(argument,),*binder.catalog.color_na,False)
