"""Select the display extension without changing base overload resolution."""

from packages.contracts import visual_display
from packages.contracts.gradient_fill import FIELDS


def register_variant(catalog, contract, pair):
    if not visual_display.has_display(contract):
        return False
    variants = (catalog.destination_calls if contract.overload_id.endswith(visual_display.DESTINATION_SUFFIX)
                else catalog.display_calls)
    variants[visual_display.base_overload_id(contract)] = pair
    return True


def check_version(binder, call):
    version = visual_display.MINIMUM_VERSION.get(call.name)
    if (version is not None and binder.catalog.language_version < version
            and any(a.name == visual_display.FIELD for a in call.arguments)):
        binder.fail('LANGUAGE_INVALID', 'VISUAL_DISPLAY_VERSION', f'{call.name} display requires Pine v{version} or later.', call.span)


def check_arguments(binder, call, expressions):
    if call.name not in visual_display.MINIMUM_VERSION:
        return
    for arg, value in zip(call.arguments, expressions, strict=True):
        if arg.name != visual_display.FIELD:
            continue
        if value.value_type.base == visual_display.DESTINATION_TYPE and call.name not in visual_display.DESTINATION_KINDS:
            binder.fail('PROFILE_UNCOVERED', 'VISUAL_DISPLAY_MODE',
                        'Display destinations are supported for plot* calls.', value.span)
        if not display_expression(binder, value):
            binder.fail('LANGUAGE_INVALID', 'VISUAL_DISPLAY_VALUE',
                        'Use supported display modes, optionally selected by a boolean input.', value.span)
    names = {arg.name for arg in call.arguments}
    if (call.name == 'fill' and visual_display.FIELD in names
            and (names.intersection(FIELDS) or sum(a.name is None for a in call.arguments) >= 6)):
        endpoints = [value for i, (arg, value) in enumerate(zip(call.arguments, expressions, strict=True))
                     if (i < 2 and arg.name is None) or arg.name in ('hline1', 'hline2')]
        if len(endpoints) == 2 and all(value.value_type.base == 'hline_id' for value in endpoints):
            binder.fail('PROFILE_UNCOVERED', 'VISUAL_DISPLAY_MODE',
                        'The gradient fill between horizontal levels has no admitted display argument.', call.span)


def display_expression(binder, value):
    from .binder import BoundBinary, BoundConditional, BoundLiteral

    if value.value_type.base not in visual_display.DISPLAY_TYPES or value.value_type.qualifier not in ('const', 'input'):
        return False
    if isinstance(value, BoundLiteral):
        return value.value in (*visual_display.VALUES, *visual_display.DESTINATION_TOKENS)
    if isinstance(value, BoundBinary):
        return (value.operator in ('+', '-') and display_expression(binder, value.left)
                and display_expression(binder, value.right))
    return (isinstance(value, BoundConditional) and boolean_condition(binder, value.condition)
            and display_expression(binder, value.when_true) and display_expression(binder, value.when_false))


def boolean_condition(binder, value):
    from .binder import BoundLiteral, BoundLoad, BoundUnary

    while isinstance(value, BoundUnary) and value.operator == 'not':
        value = value.operand
    return (isinstance(value, BoundLiteral) and type(value.value) is bool
            or isinstance(value, BoundLoad) and value.value_type.base == 'bool'
            and value.value_type.qualifier == 'input'
            and value.binding.binding_id in binder.shared.get('display_input_bindings', ()))
