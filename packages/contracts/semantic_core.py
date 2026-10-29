"""Additive reviewed syntax sugar; the closed typed IR remains fully bound."""
from .wire import read_json, digest


def policy(version):
    return {'language_version': version, 'defaults': 'scalar_literals_and_signed_numeric' if version >= 5 else 'unavailable',
        'switch': 'selector_or_conditions_first_match' if version >= 5 else 'unavailable',
        'selector': 'once_in_private_block', 'branches': 'lazy_no_fallthrough', 'max_cases': 32,
        'missing_numeric': 'na', 'missing_bool': 'false' if version == 6 else 'na',
        'missing_string_tuple': 'explicit_default_required', 'default_expressions': 'excluded',
        'arguments': 'explicit_once_in_source_order_then_closed_defaults', 'ir': 'existing_fully_bound_calls_blocks_conditions',
        'external_execution_verified': False}


def admit(root, snapshot, version):
    value = read_json(root / 'spec/semantic-core-profile.json')
    if (value.get('schema_version') != 'openpine.semantic-core-spec.1'
            or value['source_lock_sha256'] != digest(snapshot.lock)
            or value['versions'][str(version)] != {'policy': policy(version), 'reference_sha256': digest(snapshot.references[version])}):
        raise ValueError('Semantic core differs from its pinned version profile.')
