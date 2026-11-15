"""Versioned Pine contracts for prices on a frozen instrument's tick grid."""

from dataclasses import replace

from .model import CallContract, LiteralIR, ValueType
from .wire import digest


ROUND = 'environment.round_to_mintick'
FORMAT = 'environment.format_mintick'
FORMAT_TOKEN = 'format.mintick'
ROUND_NAMES = {4: 'round_to_mintick', 5: 'math.round_to_mintick', 6: 'math.round_to_mintick'}


def rounding_contract(version):
    name = ROUND_NAMES[version]
    identifier = f'openpine.tick-price.v{version}.round'
    parameter = 'x' if version == 4 else 'number'
    signature = {'name': name, 'parameter': parameter, 'rule': 'scalar_simple', 'grid': 'frozen_mintick'}
    return parameter, CallContract(
        'symbol.' + identifier, 'overload.' + identifier, 'assertion.' + identifier, digest(signature),
        ROUND, (f'parameter.{identifier}.{parameter}',), (('int', 'float'),), ('series',),
        None, (), ValueType('float'), 'scalar_simple',
    )


def formatting_contract(base, version):
    identifier = f'openpine.tick-price.v{version}.format'
    return replace(base, symbol_id='symbol.' + identifier, overload_id='overload.' + identifier,
                   assertion_id='assertion.' + identifier, operation=FORMAT,
                   signature_sha256=digest({'base': base.signature_sha256, 'format': FORMAT_TOKEN,
                                            'grid': 'frozen_mintick'}),
                   qualifier_limits=('series', 'const'))


def validate_call(call):
    if any(argument.origin != 'source' for argument in call.arguments):
        raise ValueError('Tick-price arguments must originate in the source.')
    if call.operation == FORMAT:
        token = call.arguments[1].expression
        if not isinstance(token, LiteralIR) or token.value != FORMAT_TOKEN or token.value_type != ValueType('string'):
            raise ValueError('Tick formatting requires the bound format.mintick constant.')
