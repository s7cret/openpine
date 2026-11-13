"""Bind the pinned rolling statistics while preserving numeric source types."""

from dataclasses import replace

from packages.contracts.drawings import contracts
from packages.contracts.window_statistics import EFFECTS, FIRST_VERSION, TYPED, parameters
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
        name = operation.split('.')[1]
        spelling = ('ta.' if version >= 5 else '') + name
        fields = [dict(name=n, types=t, qualifier=q) for n, t, q in parameters(operation, version)]
        rows = [row for row in entries if row['name'] == spelling and row['category'] == 'functions']
        expected = 2 if operation in TYPED and version >= 5 else 1
        if (len(rows) != expected or
                any([p['name'] for p in row['parameters']] != [p['name'] for p in fields] for row in rows)):
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {spelling} in Pine v{version}.')
        if operation in TYPED:
            result_types = {t.replace('[', ' ').replace(']', '') for row in rows for t in row['result_type_spellings']}
            if not {'series float', 'series int'} <= {t.replace('integer', 'int') for t in result_types}:
                raise SpecError('SPEC_EVIDENCE', f'Unexpected result types for {spelling} in Pine v{version}.')
        identifier = f'openpine.window-statistics.v{version}.{name}'
        api = dict(id=identifier, name=spelling, operation=operation, parameters=fields, defaults=[],
                   named=version >= 3, effects=list(EFFECTS), result='float',
                   source_record_sha256=[digest(row) for row in rows])
        contract = contracts((api,))[identifier]
        if operation in TYPED:
            contract = replace(contract, result_rule='source_series')
        catalog.contracts[contract.overload_id] = contract
        catalog.signatures[spelling] = [(signature(api, contract), contract)]
