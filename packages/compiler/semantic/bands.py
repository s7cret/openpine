"""Bind direct-global visual declarations and the additive nz(color) overload."""
from packages.contracts.bands import apis,contracts,constants,admit
from packages.contracts.visual_panes import ABI_SUFFIX, FLAG
from .colors import signature
from .specification import ROOT,SpecError

def install(catalog):
    if 'close' not in catalog.references:return
    # Core runtime policy checks precede Catalog construction in Session.
    try:admit(ROOT,catalog.snapshot,catalog.language_version)
    except ValueError as e:raise SpecError('SPEC_EVIDENCE',str(e)) from e
    registered=contracts(catalog.language_version);catalog.band_calls={}
    catalog.color_constants.update(constants(catalog.language_version))
    for a in apis(catalog.language_version):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        pair=(signature(a,c),c)
        if a['id'].endswith(ABI_SUFFIX):catalog.band_plot_overlay=pair
        else:catalog.band_calls[a['operation']]=pair
        if a['name'] in ('fill','hline'):catalog.signatures.setdefault(a['name'],[]).append(pair)

def handle_call(binder,call):
    if call.name=='plot' and any(a.name==FLAG for a in call.arguments):
        if binder.catalog.language_version<5:
            binder.fail('LANGUAGE_INVALID','VISUAL_OVERLAY_VERSION','force_overlay requires Pine v5 or later.',call.span)
        pair=binder.catalog.band_plot_overlay
    else:pair=binder.catalog.band_calls['bands.'+call.name]
    expressions=tuple(binder.expression(a.expression) for a in call.arguments)
    return binder.call(call,expressions,*pair,True)

def nz_call(binder,call,arguments):
    from .binder import BoundNA
    if call.name!='nz' or not hasattr(binder.catalog,'band_calls'):return None
    if not any(a.value_type.base=='color' for a in arguments):return None
    return binder.call(call,arguments,*binder.catalog.band_calls['bands.nz'],False)
