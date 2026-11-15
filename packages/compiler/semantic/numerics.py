"""Install reviewed math APIs while verifying the unchanged legacy numeric ABI."""
from packages.contracts.numeric_functions import LEGACY_OPERATIONS, MATH_CONSTANTS, contracts, new_contracts, registered_apis
from packages.contracts.numeric_targets import admit_profile
from .specification import ROOT,SpecError

def install(catalog):
    if 'close' not in catalog.references:return
    version=catalog.language_version
    try:admit_profile(ROOT,catalog.snapshot,version)
    except ValueError as error:raise SpecError('SPEC_EVIDENCE',str(error)) from error
    expected={k:v for k,v in contracts(version).items() if v.operation in LEGACY_OPERATIONS}
    actual={k:v for k,v in catalog.contracts.items() if v.operation in LEGACY_OPERATIONS}
    if actual!=expected:raise SpecError('SPEC_EVIDENCE','Legacy numeric ABI differs from the reviewed baseline.')
    if version >= 4:
        catalog.constants.update(MATH_CONSTANTS)
    registered=new_contracts(version)
    for a in registered_apis(version):
        c=registered[a['id']];catalog.contracts[c.overload_id]=c
        signature={'parameters':[{'name':n,'parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}} for n,pid in zip(a['parameters'],c.parameter_ids,strict=True)],'binding':{'named':a['named']},'effects':[]}
        catalog.signatures.setdefault(a['name'], []).append((signature,c))
