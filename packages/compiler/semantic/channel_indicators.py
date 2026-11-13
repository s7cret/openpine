"""Add pinned channel signatures without changing the fixed BB contract."""

from packages.contracts import CallContract, TupleType, ValueType
from packages.contracts.channel_indicators import BB, BBW, KC, KCW, EFFECTS, MARKET
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    catalog.channel_windows = {}
    version = catalog.language_version
    if version < 4 or 'close' not in catalog.references:
        return

    def fields(names, base, qualifier):
        return [dict(name=name, types=[base], qualifier=qualifier) for name in names]

    def register(name, operation, fields_, references, *, suffix='', default=False):
        spelling = ('ta.' if version >= 5 else '') + name
        identifier = f'openpine.channels.v{version}.{name}{suffix}'
        result = ValueType('float', 'series')
        if operation in (BB, KC):
            result = TupleType((result, result, result))
        implicit = fields(MARKET, 'float', 'series') if operation in (KC, KCW) else []
        defaults = [True] if default else []
        evidence = dict(id=identifier, name=spelling, operation=operation, parameters=fields_,
                        defaults=defaults, named=True, effects=list(EFFECTS),
                        source_records=[digest(row) for row in references])

        def contract(params, id_):
            return CallContract(
                'symbol.' + identifier, 'overload.' + id_, 'assertion.' + identifier,
                digest(evidence), operation, tuple(f'parameter.{id_}.{p["name"]}' for p in params),
                tuple(tuple(p['types']) for p in params), tuple(p['qualifier'] for p in params),
                None, EFFECTS, result, 'fixed_series',
            )

        intrinsic = contract(implicit + fields_, identifier)
        catalog.contracts[intrinsic.overload_id] = intrinsic
        public = intrinsic
        if implicit:
            public = contract(fields_, identifier + '.public')
            catalog.technical[public.overload_id] = ({'implicit': MARKET}, intrinsic)
        pair = (signature(evidence, public), public)
        if operation == BB:
            ((_, fixed),) = catalog.signatures[spelling]
            catalog.channel_windows[fixed.overload_id] = pair
        else:
            catalog.signatures.setdefault(spelling, []).append(pair)

    for name, operation in (('bb', BB), ('bbw', BBW), ('kc', KC), ('kcw', KCW)):
        spelling = ('ta.' if version >= 5 else '') + name
        rows = [row for row in catalog.snapshot.references[version]['entries']
                if row['name'] == spelling and row['category'] == 'functions']
        by_arity = {len(row['parameters']): row for row in rows}
        keltner = operation in (KC, KCW)
        expected = {3, 4} if keltner and version == 5 else {4} if keltner else {3}
        if len(rows) != len(expected) or by_arity.keys() != expected:
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signatures for {spelling} in Pine v{version}.')
        for arity, row in by_arity.items():
            names = ('series', 'length', 'mult', 'useTrueRange')[:arity]
            if tuple(p['name'] for p in row['parameters']) != names:
                raise SpecError('SPEC_EVIDENCE', f'Unexpected parameters for {spelling} in Pine v{version}.')
            if arity == 4 and row['parameters'][3]['default_claim']['spellings'] != ['true']:
                raise SpecError('SPEC_EVIDENCE', f'Unexpected default for {spelling} in Pine v{version}.')

        params = (fields(('series',), 'float', 'series') +
                  fields(('length',), 'int', 'simple' if keltner else 'series') +
                  fields(('mult',), 'float', 'simple'))
        if keltner:
            explicit = params + fields(('useTrueRange',), 'bool', 'simple')
            if version == 5:
                register(name, operation, params, (by_arity[3],), suffix='.short')
                register(name, operation, explicit, (by_arity[4],), suffix='.explicit')
            else:
                register(name, operation, explicit, (by_arity[4],), default=True)
        else:
            register(name, operation, params, rows)


def select(catalog, call, expressions, signature, contract):
    candidate = catalog.channel_windows.get(contract.overload_id)
    if candidate:
        for index, (argument, expression) in enumerate(zip(call.arguments, expressions, strict=True)):
            if (argument.name == 'length' or argument.name is None and index == 1
                    ) and expression.value_type.qualifier == 'series':
                return candidate
    return signature, contract
