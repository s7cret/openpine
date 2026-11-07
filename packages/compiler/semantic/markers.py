"""Install versioned declarative signal signatures."""
from packages.contracts.markers import apis,contracts,constants,admit,WINDOW_SUFFIX
from packages.contracts import visual_ohlc
from packages.contracts.visual_panes import ABI_SUFFIX
from .colors import signature
from .specification import ROOT,SpecError

def install(catalog):
    if 'close' not in catalog.references:return
    v=catalog.language_version
    try:
        admit(ROOT,catalog.snapshot,v)
        visual_ohlc.admit(ROOT,catalog.snapshot,v)
    except ValueError as e:raise SpecError('SPEC_EVIDENCE',str(e)) from e
    registered=contracts(v);catalog.color_constants.update(constants(v));catalog.signal_overlays={};catalog.signal_windows={}
    for a in apis(v):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        pair=(signature(a,c),c)
        if WINDOW_SUFFIX in a['id']:catalog.signal_windows[a['name']]=pair
        elif a['id'].endswith(ABI_SUFFIX):catalog.signal_overlays[a['name']]=pair
        else:catalog.signatures[a['name']]=[pair]
    ohlc_contracts=visual_ohlc.contracts(v)
    for api in visual_ohlc.apis(v):
        contract=ohlc_contracts[api['id']]
        catalog.contracts[contract.overload_id]=contract
        pair=(signature(api,contract),contract)
        if WINDOW_SUFFIX in api['id']:catalog.signal_windows[api['name']]=pair
        else:catalog.signatures[api['name']]=[pair]
