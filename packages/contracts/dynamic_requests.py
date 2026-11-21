"""Finite market targets for a series-valued request in the saved IR."""
from dataclasses import replace

from .model import ConditionalIR, LiteralIR, LoadIR, StoreIR, UserCallIR, ValueType
from .wire import digest


SUFFIX = '.dynamic'
MAX_MARKETS = 32
MAX_ROUTES = 16 * MAX_MARKETS


def has_dynamic_targets(program):
    from .context_ir import ir_nodes
    return program is not None and any(getattr(n, 'overload_id', '').endswith(SUFFIX)
                                       for n in ir_nodes(program.statements))


def additional_market_limit(program):
    return MAX_MARKETS if has_dynamic_targets(program) else 7


def check_market_count(count, program):
    if count > MAX_MARKETS:
        from .model import Diagnostic, OpenPineError
        raise OpenPineError(Diagnostic('INPUT_INVALID', 'DYNAMIC_CONTEXT_LIMIT',
            'Dynamic requests exceed the 32 distinct market contexts available to this run.',
            language_version=program.identity.language_version))


def caller_arguments(request):
    """Only dynamic selectors execute on the calling chart's clock."""
    return request.arguments[:2] if request.overload_id.endswith(SUFFIX) else ()


def variant(base):
    return replace(base, overload_id=base.overload_id + SUFFIX, assertion_id=base.assertion_id + SUFFIX,
                   signature_sha256=digest({'base': base.signature_sha256, 'profile': 'openpine.dynamic-requests.1'}),
                   qualifier_limits=('series', 'series', *base.qualifier_limits[2:]))


def route_site(site, symbol, timeframe):
    return f'{site}@{len(symbol)}:{symbol}:{timeframe}'


def expand(program, call, settings=None, *, primary=None, shape_only=False):
    from .prepared_requests import validate_pair
    from .primary_targets import resolve, shape

    if not program.identity.dynamic_requests_enabled:
        raise ValueError('Dynamic request targets require an enabled declaration.')
    from .context_ir import ir_nodes
    bindings = {}
    mutated = set()
    for node in ir_nodes(program.statements):
        if isinstance(node, StoreIR):
            if not node.declaration:
                mutated.add(node.binding.binding_id)
            elif node.binding.mode == 'bar':
                bindings[node.binding.binding_id] = node.expression
        elif isinstance(node, UserCallIR):
            for parameter, arg in zip(node.parameters, node.arguments, strict=True):
                bindings[parameter.binding_id] = arg.expression
    for binding_id in mutated:
        bindings.pop(binding_id, None)
    resolved = {}

    def values(node, seen, kind, local):
        if len(seen) > 64:
            raise ValueError('Dynamic target has too many dependent bindings.')
        if isinstance(node, LoadIR) and node.binding_id in local:
            if node.binding_id in seen:
                raise ValueError('Dynamic target contains a cyclic parameter binding.')
            return values(local[node.binding_id], seen | {node.binding_id}, kind, local)
        if isinstance(node, UserCallIR):
            frame = dict(local)
            frame.update((p.binding_id, a.expression) for p, a in zip(node.parameters, node.arguments, strict=True))
            for statement in node.statements:
                if not isinstance(statement, StoreIR) or not statement.declaration or statement.binding.mode != 'bar':
                    raise ValueError('Dynamic target helper requires immutable local declarations.')
                frame[statement.binding.binding_id] = statement.expression
            return values(node.result, seen, kind, frame)
        if node.value_type.qualifier != 'series':
            if shape_only:
                shape(program, node, allow_functions=True, allow_scalars=True, allow_numeric=True)
                return set()
            value, _, _ = resolve(program, node, kind, settings, primary=primary,
                                  allow_functions=True, allow_scalars=True, allow_numeric=True)
            return {(value, frozenset())}
        if isinstance(node, ConditionalIR):
            choices = set()
            condition = node.condition
            # Both axes observe the same value for an immutable chart binding.
            # A helper-local or reassigned binding cannot establish that identity.
            shared = (isinstance(condition, LoadIR) and condition.binding_id in bindings
                      and condition.binding_id not in local)
            for branch, truth in ((node.when_true, True), (node.when_false, False)):
                for value, guards in values(branch, seen, kind, local):
                    if shared:
                        if (condition.binding_id, not truth) in guards:
                            continue
                        guards = guards | {(condition.binding_id, truth)}
                    choices.add((value, guards))
            check_market_count(len({value for value, _ in choices}), program)
            return choices
        if isinstance(node, LoadIR) and node.binding_id in bindings and node.binding_id not in seen:
            key = (kind, node.binding_id)
            if key not in resolved:
                resolved[key] = values(bindings[node.binding_id], seen | {node.binding_id}, kind, {})
            return resolved[key]
        raise ValueError('Dynamic target needs a finite choice of literal market symbols or intervals.')
    symbols, intervals = (values(a.expression, set(), kind, {})
                         for a, kind in zip(call.arguments[:2], ('symbol', 'timeframe'), strict=True))
    pairs = set()
    for symbol, symbol_guards in symbols:
        for timeframe, interval_guards in intervals:
            if all((key, not truth) not in interval_guards for key, truth in symbol_guards):
                pairs.add((symbol, timeframe))
                check_market_count(len(pairs), program)
    routes = []
    for symbol, timeframe in sorted(pairs):
        args = tuple(replace(a, expression=LiteralIR(v, ValueType('string'), a.expression.span))
                     for a, v in zip(call.arguments[:2], (symbol, timeframe), strict=True))
        route = replace(call, call_site_id=route_site(call.call_site_id, symbol, timeframe),
                        arguments=(*args, *call.arguments[2:]))
        validate_pair(route)
        routes.append(route)
    return routes
