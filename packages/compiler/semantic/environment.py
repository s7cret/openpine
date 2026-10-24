"""Install only version-reviewed environment references and UTC functions."""

from packages.contracts import CallContract,ReferenceContract,ValueType
from packages.contracts.environment import environment_policy,environment_references,environment_apis
from .specification import SpecError,value_sha


def install(catalog):
    release=catalog.snapshot.environment
    if not release or 'close' not in catalog.references:return
    row=release['versions'][str(catalog.language_version)]
    if row['policy']!=environment_policy(catalog.language_version):raise SpecError('IMPLEMENTATION_MISSING','Environment policy differs.')
    for ref in row['references']:
        identity=f"openpine.environment.v{catalog.language_version}.reference.{ref['name']}"
        catalog.references[ref['name']]=ReferenceContract('symbol.'+identity,'assertion.'+identity,value_sha(ref),ref['slot'],ValueType(ref['base'],ref['qualifier']))
    for api in row['apis']:
        oid='overload.'+api['id'];names=api['parameters']
        contract=CallContract('symbol.'+api['id'],oid,'assertion.'+api['id'],value_sha(api),api['operation'],
            tuple('parameter.'+oid+'.'+n for n in names),tuple(('int',) for _ in names),tuple('series' for _ in names),
            None,(),ValueType('int'),'derived')
        signature={'parameters':[{'name':n,'parameter_id':pid,'requiredness':{'state':'required'},'default':{'kind':'none'}} for n,pid in zip(names,contract.parameter_ids,strict=True)],'binding':{'named':api['named']},'effects':[]}
        if not any(c and c.operation.startswith('environment.') for _,c in catalog.signatures.get(api['name'],[])):catalog.signatures[api['name']]=[]
        catalog.signatures[api['name']].append((signature,contract));catalog.contracts[oid]=contract
