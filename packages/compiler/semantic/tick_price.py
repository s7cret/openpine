"""Bind tick arithmetic and recognize the constant format in tostring calls."""

from dataclasses import replace

from packages.contracts.tick_price import FORMAT_TOKEN, ROUND_NAMES, formatting_contract, rounding_contract
from .specification import SpecError


def install(catalog):
    version = catalog.language_version
    catalog.tick_format = None
    if version not in ROUND_NAMES or 'close' not in catalog.references:
        return
    name = ROUND_NAMES[version]
    parameter, rounding = rounding_contract(version)
    entries = catalog.snapshot.references[version]['entries']
    rows = [row for row in entries if row['name'] == name and row['category'] == 'functions']
    if (len(rows) != (1 if version == 4 else 2)
            or any([p['name'] for p in row['parameters']] != [parameter] for row in rows)
            or not any(row['name'] == FORMAT_TOKEN and row['category'] == 'constants'
                       and row['result_type_spellings'] == ['const string'] for row in entries)):
        raise SpecError('SPEC_EVIDENCE', f'Tick-price references differ in Pine v{version}.')
    signature = {'parameters': [{'name': parameter, 'parameter_id': rounding.parameter_ids[0],
                                 'requiredness': {'state': 'required'}, 'default': {'kind': 'none'}}],
                 'binding': {'named': True}, 'effects': []}
    catalog.signatures[name] = [(signature, rounding)]
    catalog.contracts[rounding.overload_id] = rounding
    string_name = 'tostring' if version == 4 else 'str.tostring'
    _, base = next(pair for pair in catalog.signatures[string_name] if pair[1].operation == 'scalar.tostring.format')
    catalog.tick_format = formatting_contract(base, version)
    catalog.contracts[catalog.tick_format.overload_id] = catalog.tick_format
    catalog.constants[FORMAT_TOKEN] = FORMAT_TOKEN


def lower(binder, bound):
    from .binder import BoundLiteral, BoundLoad

    contract = binder.catalog.tick_format
    if bound.contract.operation != 'scalar.tostring.format' or contract is None:
        return bound
    source, argument = bound.arguments
    literal = argument.expression
    bindings = binder.shared.get('constant_bindings', {})
    while isinstance(literal, BoundLoad) and literal.value_type.qualifier == 'const':
        literal = bindings.get(literal.binding.binding_id)
    if not isinstance(literal, BoundLiteral) or literal.value != FORMAT_TOKEN:
        return bound
    return replace(bound, contract=contract, arguments=(source, replace(
        argument, expression=replace(literal, span=argument.expression.span))))
