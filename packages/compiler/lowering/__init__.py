"""Lower already bound expressions without resolving names again."""

from packages.compiler.semantic.binder import (
    BoundBinary, BoundCall, BoundConditional, BoundConversion, BoundExpression, BoundIf,
    BoundLiteral, BoundLoad, BoundNA, BoundReference, BoundStore, BoundUnary, BoundHistory,
    BoundBlock, BoundEvaluate, BoundUserCall,BoundInput,BoundTuple,BoundUnpack, BoundFor, BoundWhile, BoundLoopControl, BoundArray, BoundForIn,
)
from packages.contracts import (
    ArgumentIR, BinaryIR, CallIR, ConditionalIR, ConvertIR, IfIR, LiteralIR, LoadIR,
    NAIR, ProgramIR, ReferenceIR, StoreIR, UnaryIR, HistoryIR, BlockIR, EvaluateIR, UserCallIR,InputIR,TupleIR,UnpackIR, ForIR, WhileIR, LoopControlIR, ArrayIR, ForInIR,
)
from packages.contracts.scopes import site_id


def lower(expression: BoundExpression):
    if isinstance(expression, BoundArray):
        return ArrayIR(expression.operation, expression.element,
            tuple(ArgumentIR(a.parameter_id, lower(a.expression), a.origin, a.source_index) for a in expression.arguments),
            expression.evaluation_order, expression.value_type, site_id('array', expression.span, expression.namespace), expression.span)
    if isinstance(expression, BoundForIn):
        return ForInIR(expression.loop_id, expression.targets, lower(expression.collection),
            tuple(map(lower_statement, expression.statements)), lower(expression.result) if expression.result is not None else None,
            expression.scope_id, expression.value_type, expression.span)
    if isinstance(expression, BoundFor):
        return ForIR(expression.loop_id, expression.counter, lower(expression.initial), lower(expression.final), lower(expression.step),
            tuple(map(lower_statement, expression.statements)), lower(expression.result) if expression.result is not None else None,
            expression.scope_id, expression.value_type, expression.span)
    if isinstance(expression, BoundWhile):
        return WhileIR(expression.loop_id, lower(expression.condition), tuple(map(lower_statement, expression.statements)),
            lower(expression.result) if expression.result is not None else None, expression.scope_id, expression.value_type, expression.span)
    if isinstance(expression,BoundTuple):return TupleIR(tuple(map(lower,expression.elements)),expression.value_type,expression.span)
    if isinstance(expression,BoundInput):return InputIR(expression.definition,expression.value_type,expression.span)
    if isinstance(expression, BoundBlock):
        return BlockIR(tuple(map(lower_statement, expression.statements)), lower(expression.result),
                       expression.scope_id, expression.value_type, expression.span)
    if isinstance(expression, BoundUserCall):
        return UserCallIR(expression.function_id, expression.call_site_id, expression.parameters,
            tuple(ArgumentIR(a.parameter_id, lower(a.expression), a.origin, a.source_index) for a in expression.arguments),
            expression.evaluation_order, tuple(map(lower_statement, expression.statements)), lower(expression.result),
            expression.value_type, expression.span)
    if isinstance(expression, BoundLiteral):
        return LiteralIR(expression.value, expression.value_type, expression.span)
    if isinstance(expression, BoundNA):
        return NAIR(expression.value_type, expression.span)
    if isinstance(expression, BoundLoad):
        return LoadIR(expression.binding.binding_id, expression.value_type, expression.span)
    if isinstance(expression, BoundHistory):
        return HistoryIR(lower(expression.operand), lower(expression.offset), expression.series_id, expression.value_type, expression.span)
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
        site_id("call", expression.span, expression.namespace), expression.span,
    )


def lower_statement(statement):
    if isinstance(statement, BoundLoopControl):return LoopControlIR(statement.action, statement.loop_id, statement.span)
    if isinstance(statement,BoundUnpack):return UnpackIR(statement.bindings,lower(statement.expression),statement.span)
    if isinstance(statement, BoundEvaluate):
        return EvaluateIR(lower(statement.expression), statement.span)
    if isinstance(statement, BoundStore):
        return StoreIR(statement.binding, lower(statement.expression), statement.declaration, statement.span)
    if isinstance(statement, BoundIf):
        return IfIR(lower(statement.condition), tuple(map(lower_statement, statement.body)),
                    tuple(map(lower_statement, statement.otherwise)), statement.span)
    return lower(statement)


def lower_program(statements, identity, bindings, policy, functions=()) -> ProgramIR:
    program=ProgramIR(identity, tuple(map(lower_statement, statements)), bindings, policy, functions)
    from packages.contracts.inputs import definitions
    if definitions(program):
        from dataclasses import replace
        program=replace(program,schema_version='openpine.typed-ir.6')
    from packages.contracts.composite import uses_composite
    if uses_composite(program):
        from dataclasses import replace
        program=replace(program,schema_version='openpine.typed-ir.7')
    from packages.contracts.loops import uses_loops
    if uses_loops(program):
        from dataclasses import replace
        program=replace(program,schema_version='openpine.typed-ir.8')
    from packages.contracts.arrays import uses_arrays
    if uses_arrays(program):
        from dataclasses import replace
        program=replace(program,schema_version='openpine.typed-ir.9')
    return program
