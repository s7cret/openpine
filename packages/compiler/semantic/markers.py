"""Install versioned declarative signal signatures."""
from packages.contracts.markers import apis,contracts,constants,admit
from .colors import signature
from .specification import ROOT,SpecError

def install(catalog):
    if 'close' not in catalog.references:return
    v=catalog.language_version
    try:admit(ROOT,catalog.snapshot,v)
    except ValueError as e:raise SpecError('SPEC_EVIDENCE',str(e)) from e
    registered=contracts(v);catalog.color_constants.update(constants(v))
    for a in apis(v):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        catalog.signatures[a['name']]=[(signature(a,c),c)]
