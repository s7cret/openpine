from packages.contracts import CallContract, ValueType
from packages.contracts.tickers import PARAMETERS, names
from .specification import SpecError, value_sha


def install(catalog):
    version = catalog.language_version
    for name, operation in names(version).items():
        rows = [row for row in catalog.snapshot.references[version]['entries']
                if row['category'] == 'functions' and row['name'] == name
                and 'series string' not in row['result_type_spellings']]
        if len(rows) != 1 or not rows[0]['syntax_spellings']:
            raise SpecError('SPEC_EVIDENCE', 'Missing fixed ticker constructor signature.')
        oid = f'overload.openpine.fixed-ticker.v{version}.{operation}'
        parameters = [{'name': param, 'parameter_id': f'parameter.{oid}.{param}',
                       'requiredness': {'state': 'required'}, 'default': {'kind': 'none'}}
                      for param in PARAMETERS[operation]]
        signature = {'parameters': parameters, 'binding': {'named': version >= 3}, 'effects': []}
        contract = CallContract('symbol.' + oid, oid, 'assertion.' + oid, value_sha(rows[0]), operation,
                                tuple(p['parameter_id'] for p in parameters), (('string',),) * len(parameters),
                                ('simple',) * len(parameters), None, (), ValueType('string', 'simple'))
        catalog.contracts[oid] = contract
        catalog.signatures[name] = [(signature, contract)]
