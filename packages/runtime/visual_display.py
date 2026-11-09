"""Resolve visual visibility from constants and the launch's frozen bool inputs."""

from packages.contracts.model import BinaryIR, CallIR, ConditionalIR, InputIR, LiteralIR, LoadIR, StoreIR, UnaryIR
from packages.contracts.visual_display import DESTINATION_TOKENS, DESTINATION_TYPE, DISPLAY_TYPE, DISPLAY_TYPES
from packages.contracts.visual_display import VALUES, combine, destinations, has_display


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
        base = node.value_type.base
        if base not in DISPLAY_TYPES:
            raise ValueError('Expected a visual display expression.')
        if admitted is not None:
            admitted.add(id(node))
        if isinstance(node, LiteralIR) and node.value in (VALUES if base == DISPLAY_TYPE else DESTINATION_TOKENS):
            return node.value
        if isinstance(node, BinaryIR) and base == DESTINATION_TYPE:
            return combine(node.operator, choose(node.left, depth + 1), choose(node.right, depth + 1))
        if isinstance(node, ConditionalIR):
            yes = choose(node.when_true, depth + 1)
            no = choose(node.when_false, depth + 1)
            return yes if _condition(node.condition, inputs, depth + 1) else no
        raise ValueError('Display requires a literal or a bounded display choice.')

    value = choose(expression, 0)
    if expression.value_type.base == DESTINATION_TYPE:
        return list(destinations(value))
    return value.removeprefix('display.')


def resolve_program(program, settings):
    inputs = boolean_inputs(program, settings)
    calls = (statement.expression if isinstance(statement, StoreIR) else statement
             for statement in program.statements)
    return {node.call_site_id: resolve(node.arguments[-1].expression, inputs)
            for node in calls if isinstance(node, CallIR) and has_display(node)}
