"""Install window indicators only against the reviewed version's reference."""

from packages.contracts.drawings import contracts
from packages.contracts.window_indicators import EFFECTS, LENGTH_INDEX, NAMES, parameters
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    for name in NAMES:
        spelling = 'ta.' + name if version >= 5 else name
        operation = 'indicator_window.' + name
        names = parameters(name, version)
        row = next((r for r in entries if r['name'] == spelling), None)
        if not row or row['category'] != 'functions' or tuple(p['name'] for p in row['parameters']) != names:
            raise SpecError('SPEC_EVIDENCE', f'{spelling} reference differs in Pine v{version}.')
        fields = []
        for i, param in enumerate(names):
            if i == LENGTH_INDEX.get(operation):
                types, qualifier = ['int'], 'series' if version >= 4 else 'simple'
            elif name == 'linreg' and i == 2:
                types, qualifier = ['int'], 'simple'
            else:
                types, qualifier = ['float', 'int'], 'series'
            fields.append(dict(name=param, types=types, qualifier=qualifier))
        api = dict(id=f'openpine.window-indicators.v{version}.{spelling}', name=spelling,
                   operation=operation, parameters=fields, defaults=[], named=version >= 3,
                   result='float', effects=list(EFFECTS), source_record_sha256=digest(row))
        contract = contracts((api,))[api['id']]
        catalog.contracts[contract.overload_id] = contract
        catalog.signatures[spelling] = [(signature(api, contract), contract)]
