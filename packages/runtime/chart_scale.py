"""Read an explicitly admitted chart scale without executing presentation options."""

from packages.contracts import chart_scale as scale, visual_order
from packages.contracts.model import LiteralIR


def position(call):
    return len(call.arguments) - len(visual_order.fields(call.overload_id)) - 1


def resolve(call):
    option = call.arguments[position(call)]
    value = option.expression
    if (option.origin != 'source' or not isinstance(value, LiteralIR)
            or value.value_type.base != scale.TYPE or value.value not in scale.SCALES):
        raise ValueError('Expected a literal Pine scale constant.')
    overlay = next(arg.expression for arg in call.arguments if arg.parameter_id.endswith('.overlay'))
    if isinstance(overlay, LiteralIR):
        scale.validate(value.value, overlay.value)
    return value.value
