"""Lower already bound expressions without resolving names again."""

from packages.compiler.semantic.binder import (
    BoundBinary, BoundCall, BoundConditional, BoundConversion, BoundExpression, BoundIf,
    BoundLiteral, BoundLoad, BoundNA, BoundReference, BoundStore, BoundUnary,
)
from packages.contracts import (
    ArgumentIR, BinaryIR, CallIR, ConditionalIR, ConvertIR, IfIR, LiteralIR, LoadIR,
    NAIR, ProgramIR, ReferenceIR, StoreIR, UnaryIR,
)


def lower(expression: BoundExpression):
    if isinstance(expression, BoundLiteral):
        return LiteralIR(expression.value, expression.value_type, expression.span)
    if isinstance(expression, BoundNA):
        return NAIR(expression.value_type, expression.span)
    if isinstance(expression, BoundLoad):
        return LoadIR(expression.binding.binding_id, expression.value_type, expression.span)
    if isinstance(expression, BoundBinary):
        return BinaryIR(expression.operator, lower(expression.left), lower(expression.right), expression.value_type, expression.span)
    if isinstance(expression, BoundConditional):
        return ConditionalIR(lower(expression.condition), lower(expression.when_true), lower(expression.when_false), expression.value_type, expression.span)
    if isinstance(expression, BoundUnary):
        return UnaryIR(expression.operator, lower(expression.operand), expression.value_type, expression.span)
    if isinstance(expression, BoundReference):
        return ReferenceIR(expression.contract, expression.value_type, expression.span)
    if isinstance(expression, BoundConversion):
        return ConvertIR(lower(expression.operand), expression.value_type, expression.span)
    contract = expression.contract
    return CallIR(
        contract.symbol_id, contract.overload_id, contract.assertion_id, contract.signature_sha256,
        contract.operation,
        tuple(ArgumentIR(argument.parameter_id, lower(argument.expression), argument.origin, argument.source_index)
              for argument in expression.arguments),
        expression.evaluation_order, contract.effects, expression.value_type,
        f"call:{expression.span.start}:{expression.span.end}", expression.span,
    )


def lower_statement(statement):
    if isinstance(statement, BoundStore):
        return StoreIR(statement.binding, lower(statement.expression), statement.declaration, statement.span)
    if isinstance(statement, BoundIf):
        return IfIR(lower(statement.condition), tuple(map(lower_statement, statement.body)),
                    tuple(map(lower_statement, statement.otherwise)), statement.span)
    return lower(statement)


def lower_program(statements, identity, bindings, policy) -> ProgramIR:
    return ProgramIR(identity, tuple(map(lower_statement, statements)), bindings, policy)
