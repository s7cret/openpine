"""Admit pinned volume signatures and lower implicit OHLCV and UTC inputs."""

from packages.contracts import CallContract, TupleType, ValueType
from packages.contracts.volume_indicators import BANDS, EFFECTS, MARKET, apis, parameters
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'volume' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    for name, operation, reference in apis(version):
        spelling = ('ta.' if version >= 5 else '') + name
        slots = MARKET[operation]
        fields = [dict(name=n, types=t, qualifier='series') for n, t in parameters(operation, version)]
        names = [p['name'] for p in fields]
        category = 'variables' if reference else 'functions'
        rows = [row for row in entries if row['name'] == spelling and row['category'] == category
                and (reference or [p['name'] for p in row['parameters']] == names)]
        if len(rows) != 1:
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {spelling} in Pine v{version}.')
        identifier = f'openpine.volume.v{version}.{operation.split(".")[1]}'
        scalar = ValueType('float', 'series')
        result = TupleType((scalar,) * 3) if operation == BANDS else scalar
        api = dict(id=identifier, name=spelling, operation=operation, parameters=fields, defaults=[],
                   named=version >= 3, effects=list(EFFECTS), source_record_sha256=digest(rows[0]),
                   implicit=slots, result_arity=3 if operation == BANDS else 1)
        implicit = [dict(name=n, types=('int' if n == 'time' else 'float',), qualifier='series') for n in slots]

        def contract(params, suffix=''):
            oid = 'overload.' + identifier + suffix
            return CallContract(
                'symbol.' + identifier, oid, 'assertion.' + identifier, digest(api), operation,
                tuple(f'parameter.{oid}.{p["name"]}' for p in params), tuple(p['types'] for p in params),
                tuple(p['qualifier'] for p in params), None, EFFECTS, result, 'fixed_series',
            )

        bound = contract(implicit + fields)
        public = contract(fields, '.public')
        catalog.contracts[bound.overload_id] = bound
        catalog.technical[public.overload_id] = api, bound
        entry = signature(api, public), public
        if reference:
            catalog.technical['reference:' + spelling] = entry
        else:
            catalog.signatures.setdefault(spelling, []).append(entry)
