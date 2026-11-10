"""Install drawing operations from the pinned per-version reference."""

from packages.contracts.drawing_objects import ALL_KINDS, COPY_KINDS, copy_contract, inventory_contract
from packages.contracts.drawing_properties import OPERATIONS as PROPERTIES, property_contract
from .colors import signature
from .specification import SpecError


def install(catalog):
    version = catalog.language_version
    if version < 4 or 'close' not in catalog.references:
        return
    rows = {(row['category'], row['name']): row for row in catalog.snapshot.references[version]['entries']
            if row['name'] in COPY_KINDS or row['name'] in ALL_KINDS or row['name'] in PROPERTIES}
    try:
        for name in PROPERTIES:
            api, contract = property_contract(rows['functions', name])
            catalog.contracts[contract.overload_id] = contract
            catalog.signatures[name] = [(signature(api, contract), contract)]
        if version < 5:
            return
        for name, kind in COPY_KINDS.items():
            api, contract = copy_contract(rows['functions', name], kind)
            catalog.contracts[contract.overload_id] = contract
            catalog.signatures[name] = [(signature(api, contract), contract)]
        for name, kind in ALL_KINDS.items():
            catalog.references[name] = inventory_contract(rows['variables', name], kind)
    except (KeyError, ValueError) as error:
        raise SpecError('SPEC_EVIDENCE', f'Invalid drawing object evidence: {error}') from error
