"""Install only version-reviewed environment references and UTC functions."""

from packages.contracts import CallContract,ReferenceContract,ValueType
from packages.contracts.chart_clock import SLOTS as CHART_CLOCK_SLOTS, VERSIONS as CHART_CLOCK_VERSIONS
from packages.contracts.environment import PERIOD_V6_SLOT,environment_policy
from packages.contracts.execution_time import SPELLINGS as EXECUTION_TIME_SPELLINGS, SLOT as EXECUTION_TIME_SLOT, TIMENOW
from packages.contracts.session_flags import SLOTS as SESSION_FLAG_SLOTS, VERSIONS as SESSION_FLAG_VERSIONS
from packages.contracts.visible_range import SINCE as VISIBLE_RANGE_SINCE, SLOTS as VISIBLE_RANGE_SLOTS
from .specification import SpecError,value_sha


def install(catalog):
    release=catalog.snapshot.environment
    if not release or 'close' not in catalog.references:return
    row=release['versions'][str(catalog.language_version)]
    if row['policy']!=environment_policy(catalog.language_version):raise SpecError('IMPLEMENTATION_MISSING','Environment policy differs.')
    for ref in row['references']:
        identity=f"openpine.environment.v{catalog.language_version}.reference.{ref['name']}"
        reference = ReferenceContract('symbol.'+identity,'assertion.'+identity,value_sha(ref),ref['slot'],
                                      ValueType(ref['base'],ref['qualifier']))
        if catalog.language_version == 6 and ref['name'] == 'timeframe.period':
            catalog.legacy_references = (reference,)
            identity += '.multiplier'
            selected = {**ref, 'slot': PERIOD_V6_SLOT}
            reference = ReferenceContract('symbol.'+identity, 'assertion.'+identity, value_sha(selected),
                                          PERIOD_V6_SLOT, reference.value_type)
        catalog.references[ref['name']] = reference
    for api in row['apis']:
        oid='overload.'+api['id'];names=api['parameters']
        contract=CallContract('symbol.'+api['id'],oid,'assertion.'+api['id'],value_sha(api),api['operation'],
            tuple('parameter.'+oid+'.'+n for n in names),tuple(('int',) for _ in names),tuple('series' for _ in names),
            None,(),ValueType('int'),'derived')
        signature={'parameters':[{'name':n,'parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}} for n,pid in zip(names,contract.parameter_ids,strict=True)],'binding':{'named':api['named']},'effects':[]}
        if not any(c and c.operation.startswith('environment.') for _,c in catalog.signatures.get(api['name'],[])):catalog.signatures[api['name']]=[]
        catalog.signatures[api['name']].append((signature,contract));catalog.contracts[oid]=contract
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    for family, versions, slots, base, qualifier, v4_spelling in (
        ('chart-clock', CHART_CLOCK_VERSIONS, CHART_CLOCK_SLOTS, 'int', 'series', 'series[integer]'),
        ('session-flags', SESSION_FLAG_VERSIONS, SESSION_FLAG_SLOTS, 'bool', 'series', 'series[bool]'),
        ('visible-range', dict.fromkeys(VISIBLE_RANGE_SLOTS, VISIBLE_RANGE_SINCE), VISIBLE_RANGE_SLOTS, 'int', 'input', None),
    ):
        spelling = v4_spelling if version == 4 else f'{qualifier} {base}'
        for name, since in versions.items():
            if version < since:
                continue
            rows = [item for item in entries if item['name'] == name and item['category'] == 'variables'
                    and spelling in item['result_type_spellings']]
            if len(rows) != 1:
                raise SpecError('SPEC_EVIDENCE', f'Unverified {family} variable {name} in Pine v{version}.')
            identity = f'openpine.{family}.v{version}.{name}'
            catalog.references[name] = ReferenceContract(
                'symbol.' + identity, 'assertion.' + identity, value_sha(rows[0]), slots[name],
                ValueType(base, qualifier),
            )
    if version in EXECUTION_TIME_SPELLINGS:
        rows = [item for item in entries if item['name'] == TIMENOW and item['category'] == 'variables'
                and EXECUTION_TIME_SPELLINGS[version] in item['result_type_spellings']]
        if len(rows) != 1:
            raise SpecError('SPEC_EVIDENCE', f'Unverified execution timestamp in Pine v{version}.')
        identity = f'openpine.execution-time.v{version}.{TIMENOW}'
        catalog.references[TIMENOW] = ReferenceContract('symbol.' + identity, 'assertion.' + identity,
            value_sha(rows[0]), EXECUTION_TIME_SLOT, ValueType('int', 'series'))
    from .calendar_zones import install as install_calendar_zones
    install_calendar_zones(catalog, entries)
