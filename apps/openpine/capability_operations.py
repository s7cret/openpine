"""Shared descriptive IR features for passports and conformance observations."""
from packages.contracts import (
    BinaryIR, ConditionalIR, ConvertIR, HISTORICAL_PROFILE_ID, IfIR, LiteralIR,
    LoadIR, NAIR, OpenPineError, ReferenceIR, StoreIR, UnaryIR, HistoryIR, BlockIR, EvaluateIR, UserCallIR,
    InputIR,TupleIR,UnpackIR,ForIR,WhileIR,LoopControlIR,ArrayIR,ForInIR,ObjectIR,
)

def operations(node):
    if isinstance(node,ObjectIR):return {'object.'+node.operation} | set().union(*(operations(a.expression) for a in node.arguments))
    if isinstance(node,ArrayIR):return {'array.'+node.operation} | set().union(*(operations(a.expression) for a in node.arguments))
    if isinstance(node,ForInIR):
        result={'loop.for_in'} | operations(node.collection) | set().union(*(operations(s) for s in node.statements))
        if node.result is not None:result |= {'loop.value'} | operations(node.result)
        return result
    if isinstance(node, LoopControlIR):return {'loop.'+node.action}
    if isinstance(node, (ForIR, WhileIR)):
        result = {'loop.for' if isinstance(node, ForIR) else 'loop.while'}
        children = (node.initial,node.final,node.step) if isinstance(node,ForIR) else (node.condition,)
        for child in (*children,*node.statements):result |= operations(child)
        if node.result is not None:result |= {'loop.value'} | operations(node.result)
        return result
    if isinstance(node,TupleIR):return {'tuple.result'} | set().union(*(operations(e) for e in node.elements))
    if isinstance(node,UnpackIR):return {'tuple.unpack'} | operations(node.expression)
    if isinstance(node,InputIR):return {'input.'+node.definition.input_type}
    if isinstance(node, EvaluateIR):
        return operations(node.expression)
    if isinstance(node, (BlockIR, UserCallIR)):
        features = {"function.call", "function.parameter"} if isinstance(node, UserCallIR) else {"block.value"}
        result = features | operations(node.result) | set().union(*(operations(s) for s in node.statements))
        if isinstance(node, UserCallIR):
            result |= set().union(*(operations(a.expression) for a in node.arguments))
        return result
    if isinstance(node, LiteralIR):
        return set()
    if isinstance(node, NAIR):
        return {"value.na"}
    if isinstance(node, LoadIR):
        return {"variable.load"}
    if isinstance(node, StoreIR):
        feature = ("variable.var" if node.binding.mode == "var" else "variable.declare") if node.declaration else "variable.assign"
        return {feature} | operations(node.expression)
    if isinstance(node, IfIR):
        return {"statement.if"} | operations(node.condition) | set().union(*(operations(child) for child in (*node.body, *node.otherwise)))
    if isinstance(node, BinaryIR):
        return {f"operator.binary.{node.operator}"} | operations(node.left) | operations(node.right)
    if isinstance(node, ConditionalIR):
        return {"operator.conditional"} | operations(node.condition) | operations(node.when_true) | operations(node.when_false)
    if isinstance(node, ReferenceIR):
        return {f"context.{node.contract.slot}"}
    if isinstance(node, HistoryIR):
        return {"series.history"} | operations(node.operand) | operations(node.offset)
    if isinstance(node, ConvertIR):
        return {f"conversion.{node.operand.value_type.base}.{node.value_type.base}"} | operations(node.operand)
    if isinstance(node, UnaryIR):
        return {f"operator.unary.{node.operator}"} | operations(node.operand)
    return {node.operation} | set().union(*(operations(argument.expression) for argument in node.arguments))

