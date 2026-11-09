"""Resolve drawing store capacities once from the admitted declaration."""

from packages.contracts import drawing_limits
from packages.contracts.model import LiteralIR


def validate_call(call):
    explicit = False
    for name, argument in drawing_limits.arguments(call):
        expression = argument.expression
        if argument.origin == 'source':
            explicit = True
            if isinstance(expression, LiteralIR):
                drawing_limits.validate(name, expression.value)
        elif (not isinstance(expression, LiteralIR) or type(expression.value) is not int
              or expression.value != drawing_limits.DEFAULT):
            raise ValueError(f'Invalid default for {name}.')
    if not explicit:
        raise ValueError('Drawing limits overload requires an explicit option.')


def resolve(call, evaluate):
    capacities = dict.fromkeys(drawing_limits.UPPER, drawing_limits.DEFAULT)
    for name, argument in drawing_limits.arguments(call):
        value = evaluate(argument.expression)
        drawing_limits.validate(name, value)
        capacities[drawing_limits.KINDS[name]] = value
    return capacities
