"""Admit the pinned range and cumulative extrema signatures."""

from dataclasses import replace

from packages.contracts.drawings import contracts
from packages.contracts.extrema import EFFECTS, MAXIMUM, MINIMUM, SPREAD
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    version = catalog.language_version
    if version < 4 or 'close' not in catalog.references:
        return
    entries = catalog.snapshot.references[version]['entries']
    operations = (SPREAD,) if version == 4 else (SPREAD, MAXIMUM, MINIMUM)
    for operation in operations:
        member = operation.removeprefix('extrema.')
        name = member if version == 4 else 'ta.' + member
        is_range = operation == SPREAD
        parameters = [dict(name='source', types=('int', 'float'), qualifier='series')]
        if is_range:
            parameters.append(dict(name='length', types=('int',), qualifier='series'))
        rows = [row for row in entries if row['name'] == name and row['category'] == 'functions']
        names = [p['name'] for p in parameters]
        expected = ({'series[float]', 'series[integer]'} if version == 4 else
                    {'series float', 'series int'} if is_range else {'series float'})
        count = 2 if is_range and version >= 5 else 1
        if (len(rows) != count or {v for row in rows for v in row['result_type_spellings']} != expected
                or any([p['name'] for p in row['parameters']] != names for row in rows)
                or is_range and any(('series[integer]' if version == 4 else 'series int')
                                    not in row['parameters'][1]['type_spellings'] for row in rows)):
            raise SpecError('SPEC_EVIDENCE', f'{name} reference differs in Pine v{version}.')
        api = dict(id=f'openpine.extrema.v{version}.{member}', name=name, operation=operation,
                   parameters=parameters, defaults=[], named=True, result='float', effects=EFFECTS,
                   source_record_sha256=digest(rows))
        if is_range:
            api['result_rule'] = 'source_series'
        contract = contracts((api,))[api['id']]
        if is_range:
            contract = replace(contract, result_rule='source_series')
        catalog.contracts[contract.overload_id] = contract
        catalog.signatures[name] = [(signature(api, contract), contract)]
