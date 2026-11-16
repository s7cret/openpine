"""Bind time-based session calls separately from their environment variables."""

from dataclasses import replace

from packages.contracts.drawings import contracts
from packages.contracts.trading_sessions import (
    CALENDAR_ABI, DEFAULT_ZONE, MARKET_PARAMETERS, OFFSET_OPERATIONS, OFFSET_PARAMETERS,
    OPERATIONS, offset_parameters, public_parameters,
)
from packages.contracts.wire import digest

from .colors import signature
from .specification import SpecError


def install(catalog):
    catalog.session_offsets = {}
    if 'time' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    names = public_parameters(version)
    pinned_result = 'series' if version < 4 else 'series[integer]' if version == 4 else 'series int'
    for name, operation in OPERATIONS.items():
        if name == 'time_close' and version < 4:
            continue
        rows = [row for row in entries if row['name'] == name and row['category'] == 'functions'
                and tuple(p['name'] for p in row['parameters'][:len(names)]) == names
                and pinned_result in row['result_type_spellings']]
        if not rows:
            raise SpecError('SPEC_EVIDENCE', f'Unexpected signature for {name} in Pine v{version}.')
        parameters = [dict(name=parameter, types=['string'], qualifier='simple') for parameter in names]
        clock = [dict(name=MARKET_PARAMETERS[0], types=['int'], qualifier='series'),
                 dict(name=MARKET_PARAMETERS[1], types=['string'], qualifier='simple')]
        identifier = f'openpine.trading-sessions.v{version}.{name}'
        api = dict(id=identifier, name=name, operation=operation, parameters=clock + parameters,
                   defaults=['', DEFAULT_ZONE] if version >= 4 else [''], result='int', effects=(),
                   named=version >= 3, implicit=('time', 'period' if version < 4 else 'timeframe.period'),
                   policy='minutes_current_bar.1', source_record_sha256=[digest(row) for row in rows])
        legacy = contracts((api,))[identifier]
        catalog.contracts[legacy.overload_id] = legacy
        identifier += CALENDAR_ABI
        api.update(id=identifier, policy='utc_calendar_or_current_minute_session.1')
        intrinsic = contracts((api,))[identifier]
        public_api = dict(api, id=identifier + '.public', parameters=parameters)
        public = contracts((public_api,))[public_api['id']]
        binding = signature(public_api, public)
        if version >= 4:
            binding['parameters'][-1]['default'] = {'kind': 'reference', 'name': DEFAULT_ZONE}
        catalog.contracts[intrinsic.overload_id] = intrinsic
        catalog.technical[public.overload_id] = api, intrinsic
        catalog.signatures.setdefault(name, []).append((binding, public))
        if version >= 5:
            install_offsets(catalog, entries, name, api, parameters, clock)


def install_offsets(catalog, entries, name, base, strings, clock):
    names = offset_parameters(catalog.language_version)
    rows = [row for row in entries if row['name'] == name and row['category'] == 'functions'
            and tuple(p['name'] for p in row['parameters']) == names
            and all('series int' in p['type_spellings'] for p in row['parameters'][3:])
            and 'series int' in row['result_type_spellings']]
    if not rows:
        raise SpecError('SPEC_EVIDENCE', f'Unexpected offset signature for {name} in Pine v{catalog.language_version}.')
    offsets = [dict(name=parameter, types=['int'], qualifier='series') for parameter in names[3:]]
    api = dict(base, id=base['id'] + '.offsets', operation=OFFSET_OPERATIONS[name],
               parameters=clock + strings + offsets, defaults=['', DEFAULT_ZONE] + [0] * len(offsets),
               policy='observed_history_utc_forecast.1', source_record_sha256=[digest(row) for row in rows])
    intrinsic = contracts((api,))[api['id']]
    public_api = dict(api, id=api['id'] + '.public', parameters=strings + offsets)
    public = contracts((public_api,))[public_api['id']]
    binding = signature(public_api, public)
    binding['parameters'][2]['default'] = {'kind': 'reference', 'name': DEFAULT_ZONE}
    catalog.contracts[intrinsic.overload_id] = intrinsic
    catalog.technical[public.overload_id] = api, intrinsic
    catalog.session_offsets[name] = binding, public


def check_version(binder, expression):
    version = binder.catalog.language_version
    for argument in expression.arguments:
        if (argument.name in OFFSET_PARAMETERS
                and version < (5 if argument.name == 'bars_back' else 6)):
            binder.fail('LANGUAGE_INVALID', 'TIME_OFFSET_VERSION',
                        f'{argument.name} is unavailable in Pine v{version}.', argument.span, 'rule.environment')


def select_offsets(binder, expression, arguments, candidates):
    positional = []
    named = False
    for argument in expression.arguments:
        if argument.name is not None:
            named = True
        elif named:
            binder.fail('LANGUAGE_INVALID', 'POSITIONAL_AFTER_NAMED',
                        'Positional arguments must precede named arguments.', argument.span)
        else:
            positional.append(argument)
    start = next((i for i in range(1, min(4, len(positional)))
                  if arguments[i].value_type.base == 'int'), None)
    explicit = any(argument.name in OFFSET_PARAMETERS for argument in expression.arguments)
    if start is None and not explicit:
        return expression, candidates
    version = binder.catalog.language_version
    if version < 5:
        binder.fail('LANGUAGE_INVALID', 'TIME_OFFSET_VERSION',
                    f'Bar offsets are unavailable in Pine v{version}.', expression.span, 'rule.environment')
    if start is not None:
        tail = positional[start:]
        offsets = OFFSET_PARAMETERS[:version - 4]
        if len(tail) > len(offsets):
            binder.fail('LANGUAGE_INVALID', 'TOO_MANY_ARGUMENTS',
                        'Too many positional time offsets.', tail[len(offsets)].span)
        normalized = tuple(replace(argument, name=name) for argument, name in zip(tail, offsets))
        expression = replace(expression, arguments=(
            *positional[:start], *normalized, *expression.arguments[len(positional):],
        ))
    return expression, (binder.catalog.session_offsets[expression.name],)
