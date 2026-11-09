"""Bind formatting through the already selected declaration or plot overload."""

from dataclasses import replace

from packages.contracts import number_format as fmt
from packages.contracts.wire import digest


def install(catalog):
    catalog.format_contracts = {}
    if 'close' not in catalog.references:
        return
    if catalog.language_version >= 4:
        catalog.color_constants.update(('format.' + name, (name, 'string')) for name in fmt.DECLARATION_FORMATS)
    for contract in tuple(catalog.contracts.values()):
        fields = fmt.available(contract.operation, catalog.language_version)
        if not fields:
            continue
        suffix = fmt.SUFFIX if len(fields) == 2 else fmt.PRECISION_SUFFIX
        overload_id = contract.overload_id + suffix
        qualifier = 'const' if 'declaration' in contract.effects else 'input'
        ids = tuple(f'parameter.{overload_id}.{name}' for name in fields)
        variant = replace(contract, overload_id=overload_id, assertion_id=contract.assertion_id + suffix,
            signature_sha256=digest({'base': contract.signature_sha256, 'fields': fields,
                                     'qualifier': qualifier, 'profile': 'openpine.number-format.1'}),
            parameter_ids=(*contract.parameter_ids, *ids),
            parameter_types=(*contract.parameter_types, *((fmt.TYPES[name],) for name in fields)),
            qualifier_limits=(*contract.qualifier_limits, *((qualifier,) * len(fields))))
        catalog.contracts[overload_id] = variant
        catalog.format_contracts[contract.overload_id] = variant


def extend(catalog, signature, contract):
    variant = catalog.format_contracts.get(contract.overload_id)
    if variant is None:
        return signature, contract
    fields = fmt.fields(variant.overload_id)
    parameters = [{'name': name, 'parameter_id': pid, 'requiredness': {'state': 'optional'},
                   'default': {'kind': 'literal', 'value': fmt.DEFAULTS[name]}}
                  for name, pid in zip(fields, variant.parameter_ids[-len(fields):], strict=True)]
    return {**signature, 'parameters': [*signature['parameters'], *parameters]}, variant


def select(binder, call, signature, contract):
    declaration = 'declaration' in contract.effects
    count = sum(arg.name is None for arg in call.arguments)
    version = binder.catalog.language_version
    positional_precision = (call.name in ('study', 'indicator') and 4 <= count <= (4 if version < 4 else 5))
    named = {arg.name for arg in call.arguments} & set(fmt.FIELDS)
    if not named and not positional_precision:
        return signature, contract
    selected = extend(binder.catalog, signature, contract)
    available = fmt.fields(selected[1].overload_id)
    if not named <= set(available) or not available:
        if declaration or fmt.available(contract.operation, 6):
            binder.fail('LANGUAGE_INVALID', 'NUMBER_FORMAT_VERSION',
                        'Numeric formatting is unavailable for this Pine signature.', call.span)
    return selected


def validate(binder, name, argument, contract):
    from .binder import BoundLiteral, BoundLoad, BoundUnary

    if argument.origin == 'default':
        return
    value = argument.expression
    declaration = 'declaration' in contract.effects
    if isinstance(value, BoundLoad):
        constants = binder.shared.get('constant_bindings', {})
        value = constants.get(value.binding.binding_id, value)
    if (isinstance(value, BoundUnary) and name == 'precision' and value.operator in ('+', '-')
            and isinstance(value.operand, BoundLiteral) and type(value.operand.value) is int):
        scalar = value.operand.value if value.operator == '+' else -value.operand.value
    elif isinstance(value, BoundLiteral):
        scalar = value.value
    else:
        scalar = None
    if scalar is not None:
        try:
            fmt.check_value(name, scalar, declaration=declaration)
        except ValueError as error:
            binder.fail('LANGUAGE_INVALID', 'NUMBER_FORMAT_VALUE', str(error), value.span)
        return
    if (not declaration and isinstance(value, BoundLoad)
            and value.binding.binding_id in binder.shared.get('format_input_bindings', ())):
        return
    binder.fail('PROFILE_UNCOVERED', 'NUMBER_FORMAT_EXPRESSION',
                'Use a literal, a literal constant, or a direct plot input for numeric formatting.', value.span)
