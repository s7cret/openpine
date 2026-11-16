"""Compile constant dates and date-input defaults to admitted scalar values."""

from operator import add, mul, sub

from packages.contracts import ValueType
from packages.contracts.date_input import MAX_DATE_TEXT, VERSIONS, parse_date


INTEGER_OPERATIONS = {'+': add, '-': sub, '*': mul}


def admitted(catalog):
    version = catalog.language_version
    if version not in VERSIONS or not catalog.snapshot.inputs or 'time' not in catalog.references:
        return False
    rows = catalog.snapshot.references[version]['entries']
    return (any(row['name'] == 'timestamp' and row['category'] == 'functions'
                and any(p['name'] == 'dateString' for p in row['parameters'])
                and ('const int' in row['result_type_spellings'] or 'const integer' in row['result_type_spellings'])
                for row in rows)
            and any(row['name'] == 'input.time' and row['category'] == ('constants' if version == 4 else 'functions')
                    for row in rows))


def constant(binder, node, base, fail):
    from .binder import BoundBinary, BoundLiteral, BoundLoad, BoundUnary

    bindings = binder.shared.get('constant_bindings', {})
    resolved = {}

    def read(value, depth=0):
        def reject():
            fail(f'A bounded compile-time {base} is required.', node.span)

        if depth >= 32 or value.value_type != ValueType(base):
            reject()
        if isinstance(value, BoundLoad):
            key = value.binding.binding_id
            if key not in bindings:
                reject()
            if key not in resolved:
                resolved[key] = read(bindings[key], depth + 1)
            return resolved[key]
        if isinstance(value, BoundLiteral):
            result = value.value
        elif base == 'int' and isinstance(value, BoundUnary) and value.operator in ('+', '-'):
            operand = read(value.operand, depth + 1)
            result = operand if value.operator == '+' else -operand
        elif isinstance(value, BoundBinary) and (value.operator == '+' or base == 'int' and value.operator in INTEGER_OPERATIONS):
            result = INTEGER_OPERATIONS[value.operator](read(value.left, depth + 1), read(value.right, depth + 1))
        else:
            reject()
        if base == 'string' and len(result) > MAX_DATE_TEXT or base == 'int' and not -(2**63) <= result < 2**63:
            reject()
        return result

    return read(node)


def bind_timestamp(binder, call):
    from .binder import BoundLiteral

    if binder.catalog.language_version not in VERSIONS:
        binder.fail('LANGUAGE_INVALID', 'SIGNATURE_UNAVAILABLE', 'String dates require Pine v4 or later.', call.span)
    if not admitted(binder.catalog):
        binder.fail('SPEC_UNRESOLVED', 'DATE_PROFILE', 'Date input evidence is unavailable.', call.span)
    argument = call.arguments[0]
    if argument.name not in (None, 'dateString'):
        binder.fail('LANGUAGE_INVALID', 'UNKNOWN_ARGUMENT', 'Expected dateString.', argument.span)

    def fail(message, span):
        binder.fail('PROFILE_UNCOVERED', 'TIMESTAMP_CONST_DATE', message, span)

    text = constant(binder, binder.expression(argument.expression), 'string', fail)
    try:
        value = parse_date(text)
    except ValueError as error:
        binder.fail('LANGUAGE_INVALID', 'TIMESTAMP_DATE', str(error), argument.span)
    return BoundLiteral(value, ValueType('int'), call.span)
