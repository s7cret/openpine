"""Bind the versioned request mode without changing runtime declaration arguments."""
from dataclasses import replace

from packages.compiler.frontend import parser
from packages.contracts import ValueType
from packages.contracts.model import default_dynamic_requests


def normalize_declaration(binder, call):
    """Bind positional and named mode declarations to the same pinned fields."""
    version = binder.catalog.language_version
    if version < 5:
        return call
    mode_index = 15 if call.name == 'indicator' else 31
    positional = len(call.arguments) > mode_index and call.arguments[mode_index].name is None
    if not positional and not any(arg.name == 'dynamic_requests' for arg in call.arguments):
        return call
    row, = (r for r in binder.catalog.snapshot.references[version]['entries']
            if r['category'] == 'functions' and r['name'] == call.name)
    parameters = row['parameters']
    if parameters[mode_index]['name'] != 'dynamic_requests':
        binder.fail('SPEC_UNRESOLVED', 'DYNAMIC_REQUESTS_SIGNATURE', 'Unexpected declaration layout.', call.span)
    # These options currently have only their omitted behavior in the engine.
    # Project a literal default only; a non-default must reach a diagnostic.
    defaults = {'max_bars_back': 0, 'calc_bars_count': 0}
    if call.name == 'indicator':
        defaults.update(timeframe='', timeframe_gaps=True)
    else:
        defaults.update(shorttitle='', backtest_fill_limits_assumption=0, risk_free_rate=2,
                        fill_orders_on_standard_ohlc=False, margin_long=100 if version == 6 else 0,
                        margin_short=100 if version == 6 else 0)
    args = []
    seen = set()
    named = False
    for index, arg in enumerate(call.arguments):
        name = arg.name
        if name is None:
            if named:
                binder.fail('LANGUAGE_INVALID', 'POSITIONAL_AFTER_NAMED', 'Positional arguments must precede named arguments.', arg.span)
            if index >= len(parameters):
                binder.fail('LANGUAGE_INVALID', 'TOO_MANY_ARGUMENTS', 'Too many declaration arguments.', arg.span)
            name = parameters[index]['name']
        else:
            named = True
        if name in seen:
            binder.fail('LANGUAGE_INVALID', 'DUPLICATE_ARGUMENT', 'Argument supplied more than once.', arg.span)
        seen.add(name)
        node = arg.expression
        if name in defaults:
            default = defaults[name]
            if name == 'timeframe' and isinstance(node, parser.Reference) and node.name == 'timeframe.period':
                continue
            if (isinstance(node, parser.Literal) and node.value == default
                    and (type(node.value) is type(default) or name in ('margin_long', 'margin_short', 'risk_free_rate')
                         and type(node.value) is float)):
                continue
            binder.fail('PROFILE_UNCOVERED', 'DECLARATION_OPTION', f'Only the default {name} is supported here.', arg.span)
        if (name == 'currency' and call.name == 'strategy' and isinstance(node, parser.Reference)
                and node.name == ('syminfo.currency' if version == 6 else 'currency.NONE')):
            continue
        args.append(replace(arg, name=name) if arg.name is None else arg)
    return replace(call, arguments=tuple(args))


def check_target_mode(binder, call, expressions):
    version = binder.catalog.language_version
    if version < 5 or binder.shared.get('dynamic_requests', default_dynamic_requests(version)):
        return
    for index, (arg, expression) in enumerate(zip(call.arguments, expressions, strict=True)):
        target = arg.name in ('symbol', 'timeframe') if arg.name is not None else index < 2
        if target and expression.value_type == ValueType('string', 'series'):
            binder.fail('LANGUAGE_INVALID', 'ARGUMENT_QUALIFIER',
                        'A series symbol or timeframe requires dynamic_requests=true.', arg.span)


def bind_declaration(binder, call, expressions):
    matches = [i for i, arg in enumerate(call.arguments) if arg.name == 'dynamic_requests']
    index = matches[0]
    span = call.arguments[index].span
    if binder.catalog.language_version < 5:
        binder.fail('LANGUAGE_INVALID', 'DYNAMIC_REQUESTS_VERSION',
                    'dynamic_requests is available in Pine v5 and v6.', span)
    if len(matches) > 1:
        binder.fail('LANGUAGE_INVALID', 'DUPLICATE_ARGUMENT', 'Argument supplied more than once.', call.arguments[matches[1]].span)
    for arg in call.arguments[index+1:]:
        if arg.name is None:
            binder.fail('LANGUAGE_INVALID', 'POSITIONAL_AFTER_NAMED', 'Positional arguments must precede named arguments.', arg.span)
    node = expressions[index]
    if node.value_type != ValueType('bool'):
        binder.fail('LANGUAGE_INVALID', 'DYNAMIC_REQUESTS_TYPE', 'dynamic_requests requires a const bool.', span)
    try:
        mode = constant_bool(binder, node)
    except (ValueError, ArithmeticError):
        binder.fail('PROFILE_UNCOVERED', 'DYNAMIC_REQUESTS_CONST', 'Unsupported constant expression for dynamic_requests.', span)
    if type(mode) is not bool:
        binder.fail('LANGUAGE_INVALID', 'DYNAMIC_REQUESTS_TYPE', 'dynamic_requests requires a const bool.', span)
    binder.shared['dynamic_requests'] = mode
    return replace(call, arguments=call.arguments[:index] + call.arguments[index+1:]), expressions[:index] + expressions[index+1:]


def constant_bool(binder, node):
    from .array_order import constant
    from .binder import BoundBinary, BoundConditional, BoundLoad, BoundUnary

    if isinstance(node, BoundUnary) and node.operator == 'not':
        return not constant_bool(binder, node.operand)
    if isinstance(node, BoundBinary) and node.operator in ('and', 'or'):
        left = constant_bool(binder, node.left)
        if binder.catalog.policy.logical_evaluation == 'lazy':
            if node.operator == 'and' and not left or node.operator == 'or' and left:
                return left
        right = constant_bool(binder, node.right)
        return left and right if node.operator == 'and' else left or right
    if isinstance(node, BoundConditional):
        branch = node.when_true if constant_bool(binder, node.condition) else node.when_false
        return constant_bool(binder, branch)
    if isinstance(node, BoundLoad):
        stored = binder.shared.get('constant_bindings', {}).get(node.binding.binding_id)
        if stored is not None:
            return constant_bool(binder, stored)
    return constant(binder, node)
