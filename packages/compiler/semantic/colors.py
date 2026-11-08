"""Install the additive color ABI without changing historical plot calls."""
from packages.contracts.colors import apis,contracts,constants,admit,APPEARANCE_SUFFIX,ARCHIVE_STYLE_FIRST,PLOT_OPTIONS
from packages.contracts.visual_panes import FLAG, ABI_SUFFIX, WINDOW_SUFFIX, VIEW_FIELDS
from packages.contracts.visual_display import constants as display_constants
from .specification import ROOT,SpecError
from .visual_display import register_variant

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
    catalog.color_constants=constants(v) | display_constants(v)
    for a in apis(v):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        pair=(signature(a,c),c)
        if register_variant(catalog,c,pair):continue
        if a['name']=='na':catalog.color_na=pair
        elif a['name']=='plot':
            setattr(catalog,'color_plot'+plot_variant(a['id']),pair)
        else:catalog.signatures[a['name']]=[pair]

def styled(call):
    return call.name=='plot' and (len(call.arguments)>2 or any(a.name not in (None,'series','title') for a in call.arguments))


def plot_variant(identifier):
    if identifier.endswith(ARCHIVE_STYLE_FIRST):return '_style_first'
    if APPEARANCE_SUFFIX in identifier:return '_appearance'
    if WINDOW_SUFFIX in identifier:return '_window'
    if identifier.endswith(ABI_SUFFIX):return '_overlay'
    return ''


def plot_signature(binder,call,arguments,*,reference=False):
    catalog=binder.catalog
    version=catalog.language_version
    names={a.name for a in call.arguments}
    if FLAG in names and version<5:
        binder.fail('LANGUAGE_INVALID','VISUAL_OVERLAY_VERSION','force_overlay requires Pine v5 or later.',call.span)
    if names.intersection(VIEW_FIELDS) and version<3:
        binder.fail('LANGUAGE_INVALID','SIGNAL_WINDOW_VERSION','Named plot windows and offsets require Pine v3 or later.',call.span)
    if 'linestyle' in names and version<6:
        binder.fail('LANGUAGE_INVALID','PLOT_LINESTYLE_VERSION','plot linestyle requires Pine v6.',call.span)
    variant=''
    if version<3 and names=={None}:
        if len(arguments)>=4 and arguments[3].value_type.base=='plot_style':
            variant='_style_first'
        elif len(arguments)>6 or len(arguments)==6 and arguments[5].value_type.base=='bool':
            variant='_appearance'
    elif names.intersection(PLOT_OPTIONS):
        variant='_appearance'
    elif names.intersection(VIEW_FIELDS):
        variant='_window'
    elif FLAG in names:
        variant='_overlay'
    if reference and not variant:
        return catalog.band_calls['bands.plot']
    return getattr(catalog,('band_plot' if reference else 'color_plot')+variant)

def na_call(binder,call,argument):
    return binder.call(call,(argument,),*binder.catalog.color_na,False)
