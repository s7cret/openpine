"""Install only version-reviewed environment references and UTC functions."""

from packages.contracts import CallContract,ReferenceContract,ValueType
from packages.contracts.environment import PERIOD_V6_SLOT,environment_policy
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
    from .calendar_zones import install as install_calendar_zones
    install_calendar_zones(catalog, catalog.snapshot.references[catalog.language_version]['entries'])
