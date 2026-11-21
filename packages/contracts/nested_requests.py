"""Prove when an inner security expression runs on its enclosing candle clock."""

from .model import LiteralIR, ReferenceIR
from .primary_targets import TARGET_SLOTS


AXES = ('symbol', 'timeframe')


def private_site(node):
    from .scopes import site_id
    return site_id('call',node.span)


def private_route(node):
    from .dynamic_requests import route_site
    site = private_site(node)
    if node.overload_id.endswith('.dynamic'):
        return route_site(site,*(a.expression.value for a in node.arguments[:2]))
    return site


def private_expression(node):
    """Private evaluation uses written IR site IDs; scheduling uses per-route IDs."""
    from dataclasses import replace
    if getattr(node,'operation',None) != 'context.request':
        return node
    args = list(node.arguments)
    args[2] = replace(args[2],expression=private_expression(args[2].expression))
    return replace(node,call_site_id=private_site(node),arguments=tuple(args))


def caller_market(node):
    from .contexts import MarketContext
    symbol,timeframe = (a.expression.value for a in node.arguments[:2])
    return MarketContext(symbol,symbol,timeframe,'enclosing-request')


def specialize_tree(node, result, origins, origin, program, settings):
    from dataclasses import replace
    from .dynamic_requests import route_site
    from .calendar import requestable
    from .wire import digest
    child = node.arguments[2].expression
    if getattr(child,'operation',None) == 'context.request':
        from .prepared_requests import resolve_prepared
        from .dynamic_requests import expand
        dynamic = child.overload_id.endswith('.dynamic')
        selected = (resolve_prepared(program,child,settings,caller_market(node))
                    if child.overload_id.endswith('.prepared') else child)
        parent_axes = tuple(a.expression for a in node.arguments[:2])
        axes = market_axes(selected,parent_axes)
        args = tuple(replace(a,expression=axis) for a,axis in zip(child.arguments[:2],axes,strict=True))
        site = child.call_site_id
        if '@' in node.call_site_id:
            site += '#'+digest(node.call_site_id)
            if not dynamic:
                site = route_site(site,*(axis.value for axis in axes))
        template = replace(selected,call_site_id=site,arguments=(*args,*selected.arguments[2:]))
        variants = expand(program,template,settings,primary=caller_market(node)) if dynamic else (template,)
        expression = template
        for variant in variants:
            if not requestable(parent_axes[1].value,variant.arguments[1].expression.value):
                raise ValueError('Nested security requires a compatible enclosing interval.')
            resolved = specialize_tree(variant,result,origins,child,program,settings)
            if not dynamic:
                expression = resolved
        node = replace(node,arguments=(*node.arguments[:2],replace(node.arguments[2],expression=expression),
                                      *node.arguments[3:]))
    from .dynamic_requests import MAX_ROUTES
    if len(result) >= MAX_ROUTES:
        from .model import Diagnostic,OpenPineError
        raise OpenPineError(Diagnostic('INPUT_INVALID','DYNAMIC_CONTEXT_LIMIT',
            'Nested routes exceed the saved plan capacity.',span=node.span,language_version=program.identity.language_version))
    result.append(node)
    if origins is not None:
        origins[node.call_site_id] = origin
    return node


def child_routes(nodes):
    from .dynamic_requests import route_site
    fixed = {node.call_site_id:node for node in nodes}
    variants = {}
    for node in nodes:
        if node.overload_id.endswith('.dynamic'):
            suffix = route_site('',*(a.expression.value for a in node.arguments[:2]))
            variants.setdefault(node.call_site_id.removesuffix(suffix),[]).append(node)
    children = {}
    for parent in nodes:
        child = parent.arguments[2].expression
        if getattr(child,'operation',None) == 'context.request':
            site = child.call_site_id
            children[parent.call_site_id] = (fixed[site],) if site in fixed else tuple(variants[site])
    return children


def enclosing_intervals(nodes):
    """Timeframes on which the caller observes each independent nested request."""
    result = {}
    children = child_routes(nodes)
    for parent in nodes:
        for child in children.get(parent.call_site_id,()):
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
