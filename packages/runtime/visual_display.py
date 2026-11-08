"""Resolve visual visibility from constants and the launch's frozen bool inputs."""

from packages.contracts.model import CallIR, ConditionalIR, InputIR, LiteralIR, LoadIR, StoreIR, UnaryIR
from packages.contracts.visual_display import DISPLAY_TYPE, VALUES, has_display


def boolean_inputs(program, settings=None):
    frozen = settings['values'] if settings else {}
    return {
        statement.binding.binding_id: frozen.get(statement.expression.definition.input_id,
                                                  statement.expression.definition.default)
        for statement in program.statements
        if isinstance(statement, StoreIR) and isinstance(statement.expression, InputIR)
        and statement.expression.definition.input_type == 'bool'
    }


def _condition(expression, inputs, depth):
    if depth > 128:
        raise ValueError('Display choice exceeds the expression depth limit.')
    if isinstance(expression, LiteralIR) and type(expression.value) is bool:
        return expression.value
    if isinstance(expression, LoadIR) and expression.binding_id in inputs:
        return inputs[expression.binding_id]
    if isinstance(expression, UnaryIR) and expression.operator == 'not':
        return not _condition(expression.operand, inputs, depth + 1)
    raise ValueError('Display choices require a boolean literal or a direct bool input.')


def resolve(expression, inputs, admitted=None):
    remaining = 8192

    def choose(node, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 128:
            raise ValueError('Display choice exceeds the expression size/depth limit.')
        if node.value_type.base != DISPLAY_TYPE:
            raise ValueError('Display requires display.all or display.none.')
        if admitted is not None:
            admitted.add(id(node))
        if isinstance(node, LiteralIR) and node.value in VALUES:
            return node.value.removeprefix('display.')
        if isinstance(node, ConditionalIR):
            yes = choose(node.when_true, depth + 1)
            no = choose(node.when_false, depth + 1)
            return yes if _condition(node.condition, inputs, depth + 1) else no
        raise ValueError('Display requires a literal or a bounded display choice.')

    return choose(expression, 0)


def resolve_program(program, settings):
    inputs = boolean_inputs(program, settings)
    calls = (statement.expression if isinstance(statement, StoreIR) else statement
             for statement in program.statements)
    return {node.call_site_id: resolve(node.arguments[-1].expression, inputs)
            for node in calls if isinstance(node, CallIR) and has_display(node)}
