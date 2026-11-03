"""Install versioned Pine drawing subsets."""
from packages.contracts import labels, lines
from .colors import signature
from .specification import ROOT, SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    for profile in (labels, lines):
        try:
            profile.admit(ROOT, catalog.snapshot, version)
        except ValueError as error:
            raise SpecError('SPEC_EVIDENCE', str(error)) from error
        registered = profile.contracts(version)
        for api in profile.apis(version):
            contract = registered[api['id']]
            catalog.contracts[contract.overload_id] = contract
            catalog.signatures[api['name']] = [(signature(api, contract), contract)]
