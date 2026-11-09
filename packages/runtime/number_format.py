"""Resolve formatting from admitted IR and the run's frozen inputs."""

from packages.contracts import number_format as fmt
from packages.contracts.model import CallIR, InputIR, LiteralIR, LoadIR, StoreIR, UnaryIR


def values_for(program, settings=None):
    frozen = settings['values'] if settings else {}
    values = {}
    for statement in program.statements:
        if not isinstance(statement, StoreIR):
            continue
        node = statement.expression
        if isinstance(node, InputIR) and node.definition.input_type in ('string', 'int'):
            values[statement.binding.binding_id] = frozen.get(node.definition.input_id, node.definition.default)
        elif isinstance(node, LiteralIR) and statement.binding.value_type.qualifier == 'const':
            values[statement.binding.binding_id] = node.value
    return values


def resolve(call, values):
    fields = fmt.fields(call.overload_id)
    selected = {}
    declaration = 'declaration' in call.effects
    for name, argument in zip(fields, fmt.formatting_arguments(call), strict=True):
        expression = argument.expression
        if argument.origin == 'default':
            if not isinstance(expression, LiteralIR) or type(expression.value) is not type(fmt.DEFAULTS[name]) or expression.value != fmt.DEFAULTS[name]:
                raise ValueError('Invalid default numeric formatting argument.')
            continue
        if isinstance(expression, LiteralIR):
            value = expression.value
        elif (isinstance(expression, UnaryIR) and name == 'precision' and expression.operator in ('+', '-')
                and isinstance(expression.operand, LiteralIR) and type(expression.operand.value) is int):
            raw = expression.operand.value
            value = raw if expression.operator == '+' else -raw
        elif isinstance(expression, LoadIR) and expression.binding_id in values:
            value = values[expression.binding_id]
        else:
            raise ValueError('Unsupported numeric formatting expression.')
        fmt.check_value(name, value, declaration=declaration)
        selected[name] = value
    return fmt.NumberFormat(**selected)


def calls(program):
    for statement in program.statements:
        node = statement.expression if isinstance(statement, StoreIR) else statement
        if isinstance(node, CallIR) and fmt.fields(node.overload_id):
            yield node


def formatting(program, settings):
    selected = tuple(calls(program))
    if not selected:
        return None
    values = values_for(program, settings)
    declaration = None
    plots = []
    for call in selected:
        style = resolve(call, values)
        if 'declaration' in call.effects:
            declaration = style
        else:
            plots.append(fmt.PlotNumberFormat(call.call_site_id, style))
    return fmt.NumberFormatting(declaration, tuple(plots))
