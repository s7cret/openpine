"""Admit calendar reference forms using the exact pinned version records."""

from packages.contracts import CallContract, ReferenceContract, ValueType
from packages.contracts.calendar_zones import (
    CALENDAR_PARAMETERS, PARTS, TIMESTAMP, TIMESTAMP_PARAMETERS, WEEK, WEEK_SLOT, ZONED_PARTS,
    optional_fields,
)
from packages.contracts.wire import digest

from .specification import SpecError


def function_source(rows, name, fields):
    spelling = f'{name}({", ".join(fields)})'
    return [row for row in rows if row['name'] == name and row['category'] == 'functions'
            and any(syntax.partition(' →')[0] == spelling for syntax in row['syntax_spellings'])]


def register(catalog, name, operation, fields, rows, *, optional=(), result_rule='fixed_series'):
    version = catalog.language_version
    if not rows:
        raise SpecError('SPEC_EVIDENCE', f'Unverified calendar signature for {name} in Pine v{version}.')
    identifier = f'openpine.calendar-zones.v{version}.{operation.removeprefix("environment.")}'
    inputs = tuple('parameter.' + identifier + '.' + field for field in fields)
    defaults = {field: {'kind': 'literal', 'value': 0} for field in optional}
    fingerprint = digest({
        'parameters': fields, 'defaults': defaults, 'result_rule': result_rule,
        'policy': 'iso_week_pinned_zone_strict_wall_time.1', 'source': [digest(row) for row in rows],
    })
    contract = CallContract('symbol.' + identifier, 'overload.' + identifier, 'assertion.' + identifier,
                            fingerprint, operation, inputs,
                            tuple(('string',) if field == 'timezone' else ('int',) for field in fields),
                            ('series',) * len(fields), None, (), ValueType('int'), result_rule)
    signature = {'parameters': [
        {'name': field, 'parameter_id': parameter, 'requiredness': {'state': 'optional' if field in optional else 'required'},
         'default': defaults.get(field, {'kind': 'none'})}
        for field, parameter in zip(fields, inputs, strict=True)
    ], 'binding': {'named': version >= 3}, 'effects': []}
    catalog.contracts[contract.overload_id] = contract
    catalog.signatures.setdefault(name, []).append((signature, contract))


def install(catalog, rows):
    version = catalog.language_version
    variables = [row for row in rows if row['name'] == WEEK and row['category'] == 'variables']
    if len(variables) != 1:
        raise SpecError('SPEC_EVIDENCE', f'Unverified weekofyear variable in Pine v{version}.')
    identity = f'openpine.calendar-zones.v{version}.reference.{WEEK}'
    catalog.references[WEEK] = ReferenceContract(
        'symbol.' + identity, 'assertion.' + identity, digest(variables[0]), WEEK_SLOT, ValueType('int', 'series'),
    )
    week_parameters = ('x',) if version < 3 else ('time',)
    # Several snapshots merge the one- and two-argument result under one row.
    week_rows = [row for row in rows if row['name'] == WEEK and row['category'] == 'functions'
                 and tuple(p['name'] for p in row['parameters'][:1]) == week_parameters]
    register(catalog, WEEK, WEEK_SLOT, week_parameters, week_rows)
    if version >= 4:
        for name in PARTS:
            matches = [row for row in rows if row['name'] == name and row['category'] == 'functions'
                       and tuple(p['name'] for p in row['parameters']) == CALENDAR_PARAMETERS
                       and ('series[integer]' if version == 4 else 'series int') in row['result_type_spellings']]
            register(catalog, name, ZONED_PARTS[name], CALENDAR_PARAMETERS, matches)
    if version >= 3:
        matches = function_source(rows, 'timestamp', TIMESTAMP_PARAMETERS)
        if any(any(next(p for p in row['parameters'] if p['name'] == field)['default_claim']['spellings'] != ['0']
                   for field in optional_fields(version)) for row in matches):
            raise SpecError('SPEC_EVIDENCE', f'Unexpected timestamp defaults in Pine v{version}.')
        register(catalog, 'timestamp', TIMESTAMP, TIMESTAMP_PARAMETERS, matches,
                 optional=optional_fields(version), result_rule='scalar_simple')


def select(binder, call, arguments, candidates):
    version = binder.catalog.language_version
    timezone = any(arg.name == 'timezone' for arg in call.arguments)
    if call.name == 'timestamp':
        positional_zone = bool(arguments and call.arguments[0].name is None and arguments[0].value_type.base == 'string')
        if not (timezone or positional_zone):
            return [(binding, contract) for binding, contract in candidates if contract.operation != TIMESTAMP]
        if version >= 3:
            return [(binding, contract) for binding, contract in candidates if contract.operation == TIMESTAMP]
    elif version >= 4 or not (timezone or sum(arg.name is None for arg in call.arguments) >= 2):
        return candidates
    minimum = 3 if call.name == 'timestamp' else 4
    binder.fail('LANGUAGE_INVALID', 'CALENDAR_ZONE_VERSION',
                f'{call.name} with timezone requires Pine v{minimum} or later.', call.span, 'rule.environment')
