"""Closed request shapes shared by compiler admission and historical scheduling."""

from dataclasses import fields,is_dataclass
from .model import (CallIR,ReferenceIR,LiteralIR,ConditionalIR,BinaryIR,IfIR,UserCallIR,StoreIR,
    Diagnostic,OpenPineError,LoadIR,InputIR,ConvertIR,UnaryIR,NAIR,HistoryIR)
from .contexts import request_policy
from .calendar import timeframe as validate_timeframe


REQUEST_OPERATIONS=frozenset(('numeric.abs','numeric.nz','numeric.int','numeric.float','numeric.round',
    'numeric.min','numeric.max','series.ema','series.ema.sma_seed','series.sma','series.change',
    'series.rma','series.tr','series.atr','series.rsi','series.highest','series.lowest'))
REQUEST_SLOTS=frozenset(('open','high','low','close','volume'))


def request_input_declarations(program):
    """Keep the complete frozen settings schema in each private evaluation."""
    result=[]
    for node in program.statements:
        if not isinstance(node,StoreIR) or not node.declaration:continue
        expression=node.expression
        while isinstance(expression,ConvertIR):expression=expression.operand
        if isinstance(expression,InputIR):result.append(node)
    return tuple(result)


def request_nodes(program):
    result=[];policy=request_policy(program.identity.language_version)
    inputs={node.binding.binding_id:node for node in request_input_declarations(program)}
    modified=set()
    def assignments(value):
        if isinstance(value,StoreIR) and not value.declaration:modified.add(value.binding.binding_id)
        if is_dataclass(value):
            for f in fields(value):assignments(getattr(value,f.name))
        elif type(value) is tuple:
            for child in value:assignments(child)
    assignments(program.statements)
    def fail(message,node):
        raise OpenPineError(Diagnostic("PROFILE_UNCOVERED","REQUEST_SCOPE",message,node.span,
            "rule.context.request",program.identity.language_version))
    def expression(root):
        count=0
        def admit(node,depth=0):
            nonlocal count
            count+=1
            if count>policy['max_expression_nodes'] or depth>policy['max_expression_depth']:
                fail("Requested expression exceeds its bounded size/depth.",node)
            if isinstance(node,(LiteralIR,NAIR)):
                if node.value_type.base not in ('int','float','bool'):fail("Requested literals must be numeric or boolean arguments.",node)
            elif isinstance(node,ReferenceIR):
                if node.contract.slot not in REQUEST_SLOTS:fail("Requested references must be OHLCV fields.",node)
            elif isinstance(node,LoadIR):
                declaration=inputs.get(node.binding_id)
                if (declaration is None or node.binding_id in modified or declaration.binding.mode!='bar'
                        or declaration.binding.scope_id!='scope:root' or node.value_type.base not in ('int','float','bool')):
                    fail("Requested variables must be direct, immutable numeric/source input declarations.",node)
            elif isinstance(node,ConvertIR):
                if node.value_type.base not in ('int','float'):fail("Requested conversions must remain numeric.",node)
                admit(node.operand,depth+1)
            elif isinstance(node,UnaryIR):
                if node.operator not in ('+','-'):fail("Requested unary operators must be numeric.",node)
                admit(node.operand,depth+1)
            elif isinstance(node,BinaryIR):
                if node.operator not in ('+','-','*','/','%'):fail("Requested binary operators must be arithmetic.",node)
                admit(node.left,depth+1);admit(node.right,depth+1)
            elif isinstance(node,HistoryIR):
                if (not isinstance(node.operand,(ReferenceIR,LoadIR)) or not isinstance(node.offset,LiteralIR)
                        or type(node.offset.value) is not int or not 0<=node.offset.value<=policy['max_history_offset']):
                    fail("Requested history needs an OHLCV/source-input field and a bounded literal offset.",node)
                if isinstance(node.operand,LoadIR):
                    declaration=inputs.get(node.operand.binding_id)
                    selected=declaration.expression if declaration else None
                    while isinstance(selected,ConvertIR):selected=selected.operand
                    if not isinstance(selected,InputIR) or selected.definition.input_type!='source':
                        fail("Requested input history requires a source input.",node)
                admit(node.operand,depth+1);admit(node.offset,depth+1)
            elif isinstance(node,CallIR):
                if node.operation not in REQUEST_OPERATIONS or node.value_type.base not in ('int','float'):
                    fail("Requested calls must use the supported numeric/indicator kernels.",node)
                for arg in node.arguments:admit(arg.expression,depth+1)
            else:fail("Requested expressions exclude chart variables, user functions, branches and nested requests.",node)
        if root.value_type.base not in ('int','float'):fail("Requested expression must return a number.",root)
        admit(root)
    def walk(value,forbidden=False):
        if isinstance(value,CallIR) and value.operation=="context.request":
            if forbidden:fail("This request profile requires unconditional global requests.",value)
            args=value.arguments
            if len(args)!=len(policy["parameters"]):fail("Request parameter count differs from its version profile.",value)
            if any(not isinstance(args[i].expression,LiteralIR) or type(args[i].expression.value) is not str for i in (0,1)):
                fail("Request symbol/timeframe must be string literals.",value)
            symbol,timeframe=(args[i].expression.value for i in (0,1))
            if not symbol or ":" not in symbol or len(symbol)>256:fail("Use an explicit qualified request symbol.",value)
            try:validate_timeframe(timeframe)
            except ValueError as error:fail(str(error),value)
            expression(args[2].expression)
            for argument in args[3:]:
                flag=argument.expression
                if not isinstance(flag,LiteralIR) or (type(flag.value) is not bool if policy["flags"]=="bool" else type(flag.value) is not str or flag.value not in ("off","on")):
                    fail("Request flags differ from their version profile.",value)
            result.append(value)
            if len(result)>policy["max_requests"]:fail("At most 16 written requests are supported.",value)
            return
        forbidden=forbidden or isinstance(value,(ConditionalIR,IfIR,UserCallIR)) or (
            isinstance(value,BinaryIR) and value.operator in ("and","or")) or (
            isinstance(value,StoreIR) and value.binding.mode=="var")
        if is_dataclass(value):
            for f in fields(value):walk(getattr(value,f.name),forbidden)
        elif type(value) is tuple:
            for child in value:walk(child,forbidden)
    walk(program.statements)
    return tuple(result)


def request_flags(node,version):
    values=[a.expression.value for a in node.arguments[3:]]
    flags=[("on" if v else "off") if type(v) is bool else v for v in values]
    return flags[0],flags[1] if len(flags)>1 else request_policy(version)["lookahead_default"]
