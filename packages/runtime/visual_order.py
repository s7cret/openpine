"""Admit declaration order settings and resolve the effective policy."""

from dataclasses import fields

from packages.contracts import visual_order
from packages.contracts.model import LiteralIR, ORDERED_INDICATORS
from packages.contracts.trading import ORDERED_STRATEGIES


def declare(declaration, order):
    kind = ORDERED_INDICATORS.get(type(declaration)) or ORDERED_STRATEGIES[type(declaration)]
    return kind(**{field.name: getattr(declaration, field.name) for field in fields(declaration) if field.init},
                visual_order=order)


def validate_call(call):
    explicit = False
    for name, argument in visual_order.arguments(call):
        if argument.origin == 'source':
            explicit = True
        elif (not isinstance(argument.expression, LiteralIR)
              or argument.expression.value is not visual_order.DEFAULTS[name]):
            raise ValueError(f'Invalid default for {name}.')
    if not explicit:
        raise ValueError('Visual order overload requires an explicit option.')


def resolve(call, version, evaluate):
    fields = visual_order.available(call.operation, version)
    if not fields:
        return None
    options = {name: visual_order.DEFAULTS[name] for name in fields}
    for name, argument in visual_order.arguments(call):
        value = evaluate(argument.expression)
        if type(value) is not bool:
            raise ValueError(f'{name} requires a constant boolean value.')
        options[name] = value
    return visual_order.VisualOrder(options['explicit_plot_zorder'], options.get('behind_chart'))
