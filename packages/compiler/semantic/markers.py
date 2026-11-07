"""Install versioned declarative signal signatures."""
from packages.contracts.markers import apis,contracts,constants,admit
from packages.contracts.visual_panes import ABI_SUFFIX
from .colors import signature
from .specification import ROOT,SpecError

def install(catalog):
    if 'close' not in catalog.references:return
    v=catalog.language_version
    try:admit(ROOT,catalog.snapshot,v)
    except ValueError as e:raise SpecError('SPEC_EVIDENCE',str(e)) from e
    registered=contracts(v);catalog.color_constants.update(constants(v));catalog.signal_overlays={}
    for a in apis(v):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        pair=(signature(a,c),c)
        if a['id'].endswith(ABI_SUFFIX):catalog.signal_overlays[a['name']]=pair
        else:catalog.signatures[a['name']]=[pair]
