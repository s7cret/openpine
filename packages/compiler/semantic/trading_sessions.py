"""Bind time-based session calls separately from their environment variables."""

from packages.contracts.drawings import contracts
from packages.contracts.trading_sessions import DEFAULT_ZONE, MARKET_PARAMETERS, OPERATIONS, public_parameters
from packages.contracts.wire import digest

from .colors import signature
from .specification import SpecError


def install(catalog):
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
        intrinsic = contracts((api,))[identifier]
        public_api = dict(api, id=identifier + '.public', parameters=parameters)
        public = contracts((public_api,))[public_api['id']]
        binding = signature(public_api, public)
        if version >= 4:
            binding['parameters'][-1]['default'] = {'kind': 'reference', 'name': DEFAULT_ZONE}
        catalog.contracts[intrinsic.overload_id] = intrinsic
        catalog.technical[public.overload_id] = api, intrinsic
        catalog.signatures.setdefault(name, []).append((binding, public))
