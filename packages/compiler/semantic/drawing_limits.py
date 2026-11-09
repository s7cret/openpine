"""Bind named live drawing limits on the historical declaration."""

from dataclasses import replace

from packages.contracts import drawing_limits
from packages.contracts.wire import digest


def install(catalog):
    catalog.drawing_limit_contracts = {}
    if 'close' not in catalog.references:
        return
    for contract in tuple(catalog.contracts.values()):
        fields = drawing_limits.available(contract.operation, catalog.language_version)
        if not fields:
            continue
        suffix = drawing_limits.POLYLINE_SUFFIX if len(fields) == 4 else drawing_limits.SUFFIX
        overload_id = contract.overload_id + suffix
        variant = replace(
            contract, overload_id=overload_id, assertion_id=contract.assertion_id + suffix,
            signature_sha256=digest({'base': contract.signature_sha256, 'fields': fields,
                                     'profile': 'openpine.drawing-limits.1'}),
            parameter_ids=(*contract.parameter_ids, *(f'parameter.{overload_id}.{name}' for name in fields)),
            parameter_types=(*contract.parameter_types, *(('int',) for name in fields)),
            qualifier_limits=(*contract.qualifier_limits, *(('const',) * len(fields))),
        )
        catalog.contracts[overload_id] = variant
        catalog.drawing_limit_contracts[contract.overload_id] = variant


def extend(catalog, signature, contract):
    variant = catalog.drawing_limit_contracts.get(contract.overload_id)
    if variant is None:
        return signature, contract
    fields = drawing_limits.fields(variant.overload_id)
    parameters = [{'name': name, 'parameter_id': parameter_id, 'requiredness': {'state': 'optional'},
                   'default': {'kind': 'literal', 'value': drawing_limits.DEFAULT}}
                  for name, parameter_id in zip(fields, variant.parameter_ids[-len(fields):], strict=True)]
    return {**signature, 'parameters': [*signature['parameters'], *parameters]}, variant


def select(binder, call, signature, contract):
    if 'declaration' not in contract.effects:
        return signature, contract
    named = {argument.name for argument in call.arguments} & drawing_limits.KINDS.keys()
    if not named:
        return signature, contract
    selected = extend(binder.catalog, signature, contract)
    unsupported = named.difference(drawing_limits.fields(selected[1].overload_id))
    if unsupported:
        binder.fail('LANGUAGE_INVALID', 'DRAWING_LIMIT_VERSION',
                    f'{", ".join(sorted(unsupported))} is unavailable for this Pine signature.', call.span)
    return selected


def validate(binder, name, expression):
    from .binder import BoundLiteral, BoundNA, BoundUnary

    if isinstance(expression, BoundNA):
        value = None
    elif isinstance(expression, BoundLiteral):
        value = expression.value
    elif (isinstance(expression, BoundUnary) and expression.operator in ('+', '-')
          and isinstance(expression.operand, BoundLiteral)):
        value = expression.operand.value
        if expression.operator == '-':
            value = -value
    else:
        return
    try:
        drawing_limits.validate(name, value)
    except ValueError as error:
        binder.fail('LANGUAGE_INVALID', 'DRAWING_LIMIT_VALUE', str(error), expression.span)
