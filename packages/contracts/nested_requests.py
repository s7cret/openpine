"""Prove when an inner security expression runs on its enclosing candle clock."""

from .model import LiteralIR, ReferenceIR
from .primary_targets import TARGET_SLOTS


AXES = ('symbol', 'timeframe')


def enclosing_intervals(nodes):
    """Timeframes on which the caller observes each independent nested request."""
    result = {}
    for parent in nodes:
        child = parent.arguments[2].expression
        if getattr(child,'operation',None) == 'context.request':
            result.setdefault(child.call_site_id,set()).add(parent.arguments[1].expression.value)
    return result


def independent_depth(node):
    depth = 0
    child = node.arguments[2].expression
    while getattr(child,'operation',None) == 'context.request':
        depth += 1
        child = child.arguments[2].expression
    return depth


def inherits(target, axis):
    return ((isinstance(target, LiteralIR) and target.value == '') or
            (isinstance(target, ReferenceIR) and TARGET_SLOTS.get(target.contract.slot) == axis))


def market_axes(call, parent):
    return tuple(parent[i] if parent is not None and inherits(arg.expression, axis) else arg.expression
                 for i, (axis, arg) in enumerate(zip(AXES, call.arguments[:2], strict=True)))


def same_clock(parent, child, axes):
    from .context_ir import security_count
    from .heikin_ashi import transforms

    if (child.operation != 'context.request' or len(child.arguments) != 5 or transforms(child)
            or transforms(parent) or security_count(parent) is not None
            or any(not isinstance(a.expression, LiteralIR) or a.expression.value != 'off'
                   for a in child.arguments[3:5])):
        return False
    for axis, outer, arg in zip(AXES, axes, child.arguments[:2], strict=True):
        inner = arg.expression
        if not inherits(inner, axis) and not (isinstance(inner, LiteralIR) and
                                              isinstance(outer, LiteralIR) and inner.value == outer.value):
            return False
    return True
