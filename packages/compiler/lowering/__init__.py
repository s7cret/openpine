"""Lower already bound expressions without resolving names again."""

from packages.compiler.semantic.binder import BoundCall, BoundConversion, BoundExpression, BoundLiteral, BoundReference, BoundUnary
from packages.contracts import ArgumentIR, CallIR, ConvertIR, LiteralIR, ProgramIR, ReferenceIR, UnaryIR


def lower(expression: BoundExpression):
    if isinstance(expression, BoundLiteral):
        return LiteralIR(expression.value, expression.value_type, expression.span)
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


def lower_program(statements: tuple[BoundCall, ...], identity) -> ProgramIR:
    return ProgramIR(identity, tuple(lower(statement) for statement in statements))
