"""Admit oscillators only where the pinned Pine version provides a signature."""

from packages.contracts.drawings import contracts
from packages.contracts.momentum import EFFECTS, FIRST_VERSION, WPR, WPR_MARKET, parameters
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
        fields = [dict(name=n, types=list(t), qualifier=q) for n, t, q in parameters(operation, version)]
        rows = [row for row in entries if row['name'] == spelling and row['category'] == 'functions']
        if len(rows) != 1 or [p['name'] for p in rows[0]['parameters']] != [p['name'] for p in fields]:
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {spelling} in Pine v{version}.')
        identifier = f'openpine.momentum.v{version}.{name}'
        slots = WPR_MARKET if operation == WPR else ()
        api = dict(id=identifier, name=spelling, operation=operation, parameters=fields, defaults=[],
                   named=version >= 3, effects=list(EFFECTS), source_record_sha256=digest(rows[0]),
                   implicit=slots, result='float')
        public = contracts((api,))[identifier]
        if slots:
            implicit = [dict(name=n, types=['float'], qualifier='series') for n in slots]
            intrinsic = contracts(({**api, 'id': identifier + '.market', 'parameters': implicit + fields},))
            bound = intrinsic[identifier + '.market']
            catalog.technical[public.overload_id] = api, bound
        else:
            bound = public
        catalog.contracts[bound.overload_id] = bound
        catalog.signatures[spelling] = [(signature(api, public), public)]
