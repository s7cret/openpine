"""Admit alert functions against the pinned Pine references."""
from packages.contracts.alerts import CALL, CONDITION, DEFAULTS, FREQUENCIES, VERSIONS, valid_message
from packages.contracts.drawings import contracts
from packages.contracts.wire import digest
from .colors import signature
from .specification import SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    entries = catalog.snapshot.references[version]['entries']
    rows = {row['name']: row for row in entries
            if row['name'] in (*VERSIONS, *FREQUENCIES)}
    for name, first_version in VERSIONS.items():
        if version < first_version:
            continue
        operation, fields = (
            (CALL, (('message', 'string', 'series'), ('freq', 'string', 'input'))) if name == 'alert'
            else (CONDITION, (('condition', 'bool', 'series'), ('title', 'string', 'const'), ('message', 'string', 'const')))
        )
        row = rows.get(name)
        expected_names = [field[0] for field in fields][1 if version == 2 else 0:]
        if not row or row['category'] != 'functions' or [p['name'] for p in row['parameters']] != expected_names:
            raise SpecError('SPEC_EVIDENCE', f'Alert reference differs in Pine v{version}.')
        api = dict(id=f'openpine.alerts.v{version}.{name}', name=name, operation=operation,
                   parameters=[dict(name=n, types=[t], qualifier=q) for n,t,q in fields],
                   defaults=list(DEFAULTS[operation]), named=True, result='void',
                   effects=['declares_alert' if operation == CONDITION else 'emits_alert'],
                   source_record_sha256=digest(row))
        contract = contracts((api,))[api['id']]
        catalog.contracts[contract.overload_id] = contract
        catalog.signatures[name] = [(signature(api, contract), contract)]
    if version >= VERSIONS['alert']:
        if any(name not in rows or rows[name]['category'] != 'constants' for name in FREQUENCIES):
            raise SpecError('SPEC_EVIDENCE', 'Alert frequency references are missing.')
        catalog.color_constants.update({name: (name, 'string') for name in FREQUENCIES})


def constant_text(binder, node):
    from .binder import BoundBinary, BoundLiteral, BoundLoad

    bindings = binder.shared.get('constant_bindings', {})
    resolved = {}

    def resolve(value, depth):
        if depth < 32 and value.value_type.base == 'string' and value.value_type.qualifier == 'const':
            if isinstance(value, BoundLiteral) and valid_message(value.value):
                return value.value
            if isinstance(value, BoundLoad):
                key = value.binding.binding_id
                if key in bindings:
                    if key not in resolved:
                        resolved[key] = resolve(bindings[key], depth + 1)
                    return resolved[key]
            if isinstance(value, BoundBinary) and value.operator == '+':
                text = resolve(value.left, depth + 1) + resolve(value.right, depth + 1)
                if valid_message(text):
                    return text
        binder.fail('PROFILE_UNCOVERED', 'ALERT_TEXT',
                    'Alert titles and templates require bounded compile-time strings.', value.span)

    return resolve(node, 0)
