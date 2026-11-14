"""Admit correlation and RCI against the pinned reference rows."""

from packages.contracts.correlation import EFFECTS, PEARSON, RCI, parameters
from packages.contracts.drawings import contracts
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    definitions = [(PEARSON, 'correlation' if version < 5 else 'ta.correlation')]
    if version == 6:
        definitions.append((RCI, 'ta.rci'))
    for operation, name in definitions:
        fields = [dict(name=n, types=t, qualifier=q) for n, t, q in parameters(operation, version)]
        rows = [row for row in entries if row['name'] == name and row['category'] == 'functions']
        expected_length = ('series[integer]' if version == 4 else 'integer' if version < 4
                           else 'series int' if operation == PEARSON else 'simple int')
        if (len(rows) != 1 or
                [field['name'] for field in rows[0]['parameters']] != [p['name'] for p in fields] or
                expected_length not in rows[0]['parameters'][-1]['type_spellings']):
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {name} in Pine v{version}.')
        identifier = f'openpine.correlation.v{version}.{name.split(".")[-1]}'
        api = dict(id=identifier, name=name, operation=operation, parameters=fields, defaults=[],
                   named=version >= 3, effects=EFFECTS, result='float', source_record_sha256=digest(rows[0]))
        contract = contracts((api,))[identifier]
        catalog.contracts[contract.overload_id] = contract
        catalog.signatures[name] = [(signature(api, contract), contract)]
