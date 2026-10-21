"""Closed request shapes shared by compiler admission and historical scheduling."""

from dataclasses import fields,is_dataclass
from .model import (CallIR,ReferenceIR,LiteralIR,ConditionalIR,BinaryIR,IfIR,UserCallIR,StoreIR,
    Diagnostic,OpenPineError)
from .contexts import request_policy,timeframe_ms


def request_nodes(program):
    result=[];policy=request_policy(program.identity.language_version)
    def fail(message,node):
        raise OpenPineError(Diagnostic("PROFILE_UNCOVERED","REQUEST_SCOPE",message,node.span,
            "rule.context.request",program.identity.language_version))
    def walk(value,forbidden=False):
        if isinstance(value,CallIR) and value.operation=="context.request":
            if forbidden:fail("This request profile requires unconditional global requests.",value)
            args=value.arguments
            if len(args)!=len(policy["parameters"]):fail("Request parameter count differs from its version profile.",value)
            if any(not isinstance(args[i].expression,LiteralIR) or type(args[i].expression.value) is not str for i in (0,1)):
                fail("Request symbol/timeframe must be string literals.",value)
            symbol,timeframe=(args[i].expression.value for i in (0,1))
            if not symbol or ":" not in symbol or len(symbol)>256:fail("Use an explicit qualified request symbol.",value)
            try:timeframe_ms(timeframe)
            except ValueError as error:fail(str(error),value)
            expr=args[2].expression
            def close(node):return isinstance(node,ReferenceIR) and node.contract.slot=="close"
            if not (close(expr) or isinstance(expr,CallIR) and expr.operation in ("series.ema","series.ema.sma_seed")
                    and len(expr.arguments)==2 and close(expr.arguments[0].expression)
                    and isinstance(expr.arguments[1].expression,LiteralIR) and type(expr.arguments[1].expression.value) is int):
                fail("Requested expression must be close or EMA(close, literal length).",value)
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
