"""Install the versioned Pine label subset."""
from packages.contracts.labels import admit, apis, contracts
from .colors import signature
from .specification import ROOT, SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    try:
        admit(ROOT, catalog.snapshot, version)
    except ValueError as error:
        raise SpecError('SPEC_EVIDENCE', str(error)) from error
    registered = contracts(version)
    for api in apis(version):
        contract = registered[api['id']]
        catalog.contracts[contract.overload_id] = contract
        catalog.signatures[api['name']] = [(signature(api, contract), contract)]
