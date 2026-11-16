"""Bind time presentation without introducing calendar or market-data effects."""

import json

from packages.contracts.time_format import DEFAULTS, DEFAULT_PATTERN, DEFAULT_ZONE, NAME, NAMES, VERSIONS, contract

from .specification import SpecError


def install(catalog):
    version = catalog.language_version
    if version not in VERSIONS or DEFAULT_ZONE not in catalog.references:
        return
    entries = catalog.snapshot.references[version]['entries']
    rows = [row for row in entries if row['name'] == NAME and row['category'] == 'functions']
    if len(rows) != 1 or rows[0]['result_type_spellings'] != ['series string']:
        raise SpecError('SPEC_EVIDENCE', f'Time format result differs in Pine v{version}.')
    parameters = rows[0]['parameters']
    if tuple(row['name'] for row in parameters) != NAMES or (
            parameters[1]['default_claim']['spellings'] != [json.dumps(DEFAULT_PATTERN)]
            or parameters[2]['default_claim']['spellings'] != [DEFAULT_ZONE]
            or [row['requiredness_claim'] for row in parameters] != ['required', 'optional', 'optional']):
        raise SpecError('SPEC_EVIDENCE', f'Time format parameters differ in Pine v{version}.')
    admitted = contract(version)
    signature = {'parameters': [
        {'name': name, 'parameter_id': identifier, 'requiredness': {'state': row['requiredness_claim']},
         'default': default}
        for name, identifier, row, default in zip(NAMES, admitted.parameter_ids, parameters, DEFAULTS, strict=True)
    ], 'binding': {'named': True}, 'effects': []}
    catalog.signatures[NAME] = [(signature, admitted)]
    catalog.contracts[admitted.overload_id] = admitted
