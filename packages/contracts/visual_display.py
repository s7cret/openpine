"""Closed, additive on/off display ABI for bar-aligned visuals."""

FIELD = 'display'
ABI_SUFFIX = '.display'
DISPLAY_TYPE = 'plot_simple_display'
VALUES = ('display.all', 'display.none')
DEFAULT = VALUES[0]
MINIMUM_VERSION = {
    'plot': 4, 'plotshape': 4, 'plotchar': 4, 'plotarrow': 4, 'plotbar': 4, 'plotcandle': 4,
    'hline': 5, 'fill': 5, 'bgcolor': 5, 'barcolor': 5,
}


def constants(version):
    return {name: (name, DISPLAY_TYPE) for name in VALUES} if version >= 4 else {}


def with_display(apis, version):
    extensions = [dict(api, id=api['id'] + ABI_SUFFIX,
                       parameters=[*api['parameters'], dict(name=FIELD, types=[DISPLAY_TYPE], qualifier='input')],
                       defaults=[*api['defaults'], DEFAULT])
                  for api in apis if version >= MINIMUM_VERSION.get(api['name'], 7)
                  and api['operation'] != 'bands.gradient_hline']
    return [*apis, *extensions]


def has_display(node):
    return node.overload_id.endswith(ABI_SUFFIX)


def base_overload_id(node):
    return node.overload_id.removesuffix(ABI_SUFFIX)


def base_arguments(node):
    return node.arguments[:-1] if has_display(node) else node.arguments


def validate_call(node):
    if has_display(node) and (not node.arguments or node.arguments[-1].origin != 'source'):
        raise ValueError('Visual display requires an explicit argument.')


def split_metadata(value, version=6):
    """Validate the optional presentation field before the base schema sees it."""
    if type(value) is not dict or FIELD not in value:
        return value, None
    display = value[FIELD]
    kind = value.get('kind', 'plot')
    if (type(display) is not str or display not in ('all', 'none')
            or type(kind) is not str or version < MINIMUM_VERSION.get(kind, 7)
            or kind == 'fill' and value.get('mask') == 'hline'):
        raise ValueError('Invalid visual display metadata.')
    return {key: item for key, item in value.items() if key != FIELD}, display
