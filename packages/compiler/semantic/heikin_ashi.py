"""Install the ticker against its versioned reference record."""
from packages.contracts import CallContract, ValueType
from packages.contracts.heikin_ashi import OPERATION, public_name

from .specification import SpecError, value_sha


def install(catalog):
    version = catalog.language_version
    name = public_name(version)
    if name is None:
        return None
    rows = [row for row in catalog.snapshot.references[version]['entries'] if row['name'] == name]
    if version < 5:
        expected_result = [] if version == 2 else ['string']
    else:
        expected_result = ['simple string']
    evidence = [row for row in rows if row['result_type_spellings'] == expected_result]
    expected_parameters = [] if version == 2 else ['symbol']
    if len(evidence) != 1 or [p['name'] for p in evidence[0]['parameters']] != expected_parameters:
        raise SpecError('SPEC_EVIDENCE', 'Heikin Ashi ticker evidence differs from the historical profile.')
    overload = f'overload.openpine.heikin-ashi.v{version}.ticker'
    parameter = f'parameter.{overload}.symbol'
    evidence_sha = value_sha({'reference': evidence[0]['source_record_sha256'], 'profile': 'openpine.heikin-ashi.1',
                              'positional_reconstruction': version == 2})
    contract = CallContract(f'symbol.openpine.heikin-ashi.v{version}.ticker', overload,
                            f'assertion.openpine.heikin-ashi.v{version}.ticker', evidence_sha,
                            OPERATION, (parameter,), (('string',),), ('simple',), None, (), ValueType('string', 'simple'))
    signature = {'parameters': [{'name': 'symbol', 'parameter_id': parameter, 'requiredness': {'state': 'required'},
                                 'default': {'kind': 'none'}}], 'binding': {'named': version >= 3}, 'effects': []}
    catalog.contracts[overload] = contract
    catalog.signatures[name] = [(signature, contract)]
    return contract
