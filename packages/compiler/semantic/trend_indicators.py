"""Install the version-specific trend indicators backed by the pinned records."""

from packages.contracts import CallContract, TupleType, ValueType
from packages.contracts.trend_indicators import DMI, SUPERTREND, SAR, EFFECTS, MARKET, parameters
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    references = catalog.snapshot.references[version]['entries']
    for operation, first, arity in ((DMI, 4, 3), (SUPERTREND, 4, 2), (SAR, 1, 1)):
        if version < first:
            continue
        name = operation.split('.')[1]
        spelling = ('ta.' if version >= 5 else '') + name
        fields = [dict(name=n, types=[t], qualifier=q) for n, t, q in parameters(operation, version)]
        slots = MARKET[operation]
        implicit = [dict(name=n, types=['float'], qualifier='series') for n in slots]
        rows = [row for row in references if row['name'] == spelling and row['category'] == 'functions']
        names = [p['name'] for p in fields]
        if len(rows) != 1 or [p['name'] for p in rows[0]['parameters']] != names:
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {spelling} in Pine v{version}.')
        identifier = f'openpine.trends.v{version}.{name}'
        scalar = ValueType('float', 'series')
        result = scalar if arity == 1 else TupleType((scalar,) * arity)
        api = dict(id=identifier, name=spelling, operation=operation, parameters=fields, defaults=[],
                   named=version >= 3, effects=list(EFFECTS), source_record_sha256=digest(rows[0]),
                   implicit=slots, result_arity=arity)

        def contract(params, suffix=''):
            id_ = identifier + suffix
            return CallContract(
                'symbol.' + identifier, 'overload.' + id_, 'assertion.' + identifier, digest(api), operation,
                tuple(f'parameter.{id_}.{p["name"]}' for p in params), tuple(tuple(p['types']) for p in params),
                tuple(p['qualifier'] for p in params), None, EFFECTS, result, 'fixed_series',
            )

        intrinsic = contract(implicit + fields)
        public = contract(fields, '.public')
        catalog.contracts[intrinsic.overload_id] = intrinsic
        catalog.technical[public.overload_id] = (api, intrinsic)
        catalog.signatures[spelling] = [(signature(api, public), public)]
