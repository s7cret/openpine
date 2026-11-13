"""Bind both pivot overloads and lower the implicit physical high or low."""

from packages.contracts.drawings import contracts
from packages.contracts.pivots import EFFECTS, LEGS, MARKET, parameters
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'high' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    for operation, slot in MARKET.items():
        name = operation.split('.')[1]
        spelling = ('ta.' if version >= 5 else '') + name
        rows = [row for row in entries if row['name'] == spelling and row['category'] == 'functions']
        names = ('source', *LEGS)
        by_shape = {tuple(p['name'] for p in row['parameters']): row for row in rows}
        expected = {()} if version < 3 else {names} if version < 5 else {names, LEGS}
        if len(rows) != len(expected) or by_shape.keys() != expected:
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {spelling} in Pine v{version}.')
        if 3 <= version < 5:
            spellings = {text.partition(')')[0] for text in rows[0]['syntax_spellings']}
            if not {f'{name}({", ".join(shape)}' for shape in (names, LEGS)} <= spellings:
                raise SpecError('SPEC_EVIDENCE', f'Missing pivot overload spelling in Pine v{version}.')

        fields = [dict(name=n, types=t, qualifier=q) for n, t, q in parameters(version)]
        base = dict(name=spelling, operation=operation, defaults=[], named=version >= 3,
                    effects=list(EFFECTS), result='float')
        stem = f'openpine.pivots.v{version}.{name}'
        for short in (False, True):
            source_shape = LEGS if short and version >= 5 else names if version >= 3 else ()
            identifier = stem + ('.implicit' if short else '.source')
            api = dict(base, id=identifier, parameters=fields,
                       implicit=(slot,) if short else (),
                       source_record_sha256=digest(by_shape[source_shape]))
            bound = contracts((api,))[identifier]
            catalog.contracts[bound.overload_id] = bound
            if short:
                public_api = dict(api, id=identifier + '.public', parameters=fields[1:])
                public = contracts((public_api,))[public_api['id']]
                catalog.technical[public.overload_id] = api, bound
            else:
                public_api, public = api, bound
            catalog.signatures.setdefault(spelling, []).append((signature(public_api, public), public))
