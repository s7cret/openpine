"""Admit pinned v5/v6 signatures and bind physical time and OHLC."""

from packages.contracts.drawings import contracts
from packages.contracts.pivot_levels import CHANGE, EFFECTS, MARKET, NAMES, parameters
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    version = catalog.language_version
    if version < 5 or 'time' not in catalog.references:
        return
    entries = catalog.snapshot.references[version]['entries']
    for operation, name in NAMES.items():
        public_fields = [dict(name=key, types=(base,), qualifier='series') for key, base in parameters(operation)]
        rows = [row for row in entries if row['name'] == name and row['category'] == 'functions']
        change = operation == CHANGE
        result = 'bool' if change else 'array.float'
        pinned_result = 'series bool' if change else 'array<float>'
        if (len(rows) != 1 or [field['name'] for field in rows[0]['parameters']] != [p['name'] for p in public_fields]
                or pinned_result not in rows[0]['result_type_spellings']
                or not change and rows[0]['parameters'][-1]['default_claim']['spellings'] != ['false']):
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {name} in Pine v{version}.')
        slots = MARKET[operation]
        implicit = [dict(name=slot, types=(base,), qualifier=qualifier) for slot, base, qualifier in zip(
            slots, ('int', 'int', 'string') if change else ('float',) * len(slots),
            ('series', 'series', 'simple') if change else ('series',) * len(slots), strict=True)]
        identifier = f'openpine.pivot-levels.v{version}.{name}'
        api = dict(id=identifier, name=name, operation=operation, parameters=implicit + public_fields,
                   defaults=['' if change else False], named=True, result=result, effects=EFFECTS[operation],
                   implicit=slots, source_record_sha256=digest(rows[0]))
        bound = contracts((api,))[identifier]
        public_api = dict(api, id=identifier + '.public', parameters=public_fields)
        public = contracts((public_api,))[public_api['id']]
        catalog.contracts[bound.overload_id] = bound
        catalog.technical[public.overload_id] = api, bound
        catalog.signatures.setdefault(name, []).append((signature(public_api, public), public))
