"""Bind ALMA, HMA and COG against their pinned Pine signatures."""

from packages.contracts.drawings import contracts
from packages.contracts.weighted_indicators import ALMA, EFFECTS, FIRST_VERSION, length_qualifier, parameters
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    for operation, first in FIRST_VERSION.items():
        if version < first:
            continue
        name = operation.removeprefix('weighted.')
        spelling = 'ta.' + name if version >= 5 else name
        fields = parameters(operation, version)
        names = tuple(field[0] for field in fields)
        rows = {tuple(p['name'] for p in r['parameters']): r for r in entries
                if r['name'] == spelling and r['category'] == 'functions'}
        floor = operation == ALMA and version >= 4
        expected = {names, names[:-1]} if floor and version == 5 else {names}
        qualifier = length_qualifier(operation, version)
        length_type = (qualifier + ' int' if version >= 5 else
                       'series[integer]' if qualifier == 'series' else 'integer')
        if (rows.keys() != expected or any(length_type not in row['parameters'][1]['type_spellings'] for row in rows.values())
                or floor and rows[names]['parameters'][-1]['default_claim']['spellings'] != ['false']):
            raise SpecError('SPEC_EVIDENCE', f'{spelling} reference differs in Pine v{version}.')

        # v5 has two pinned rows. v4/v6 record the omitted slot as a default.
        candidates = [(fields, [False] if floor and version != 5 else [])]
        if floor and version == 5:
            candidates.append((fields[:-1], []))
        admitted = []
        for slots, defaults in candidates:
            key = tuple(field[0] for field in slots)
            api = dict(id=f'openpine.weighted.v{version}.{name}.{len(slots)}', name=spelling,
                       operation=operation, parameters=[dict(name=n, types=t, qualifier=q) for n, t, q in slots],
                       defaults=defaults, named=version >= 3, result='float', effects=EFFECTS,
                       source_record_sha256=digest(rows[key]))
            contract = contracts((api,))[api['id']]
            catalog.contracts[contract.overload_id] = contract
            admitted.append((signature(api, contract), contract))
        catalog.signatures[spelling] = admitted
