"""Closed display modes and destinations for bar-aligned visuals."""

FIELD = 'display'
ABI_SUFFIX = '.display'
DESTINATION_SUFFIX = '.destinations.display'
DISPLAY_TYPE = 'plot_simple_display'
DESTINATION_TYPE = 'plot_display'
DISPLAY_TYPES = (DISPLAY_TYPE, DESTINATION_TYPE)
VALUES = ('display.all', 'display.none')
DEFAULT = VALUES[0]
DESTINATIONS = ('pane', 'status_line', 'price_scale', 'data_window')
DESTINATION_TOKENS = tuple('display.' + name for name in DESTINATIONS)
PLOT_OPERATIONS = ('colors.plot', 'bands.plot')
MINIMUM_VERSION = {
    'plot': 4, 'plotshape': 4, 'plotchar': 4, 'plotarrow': 4, 'plotbar': 4, 'plotcandle': 4,
    'hline': 5, 'fill': 5, 'bgcolor': 5, 'barcolor': 5,
}


def constants(version):
    simple = {name: (name, DISPLAY_TYPE) for name in VALUES} if version >= 4 else {}
    extended = {name: (name, DESTINATION_TYPE) for name in DESTINATION_TOKENS} if version >= 5 else {}
    return {**simple, **extended}


def with_display(apis, version):
    def extension(api, suffix, kind):
        return dict(api, id=api['id'] + suffix,
                    parameters=[*api['parameters'], dict(name=FIELD, types=[kind], qualifier='input')],
                    defaults=[*api['defaults'], DEFAULT])

    extensions = [extension(api, ABI_SUFFIX, DISPLAY_TYPE)
                  for api in apis if version >= MINIMUM_VERSION.get(api['name'], 7)
                  and api['operation'] != 'bands.gradient_hline']
    if version >= 5:
        extensions.extend(extension(api, DESTINATION_SUFFIX, DESTINATION_TYPE)
                          for api in apis if api['operation'] in PLOT_OPERATIONS)
    return [*apis, *extensions]


def has_display(node):
    return node.overload_id.endswith(ABI_SUFFIX)


def base_overload_id(node):
    if node.overload_id.endswith(DESTINATION_SUFFIX):
        return node.overload_id.removesuffix(DESTINATION_SUFFIX)
    return node.overload_id.removesuffix(ABI_SUFFIX)


def base_arguments(node):
    return node.arguments[:-1] if has_display(node) else node.arguments


def validate_call(node):
    if has_display(node) and (not node.arguments or node.arguments[-1].origin != 'source'):
        raise ValueError('Visual display requires an explicit argument.')


def valid_destinations(value):
    return type(value) in (list, tuple) and tuple(value) == tuple(name for name in DESTINATIONS if name in value)


def destinations(value):
    if value == VALUES[0]:
        return DESTINATIONS
    if value == VALUES[1]:
        return ()
    if value in DESTINATION_TOKENS:
        return (value.removeprefix('display.'),)
    if valid_destinations(value):
        return tuple(value)
    raise ValueError('Invalid visual display destinations.')


def combine(operator, left, right):
    left, right = destinations(left), destinations(right)
    if operator == '+':
        return tuple(name for name in DESTINATIONS if name in left or name in right)
    if operator == '-':
        return tuple(name for name in left if name not in right)
    raise ValueError('Display destinations combine using + or -.')


def split_metadata(value, version=6):
    """Validate the optional presentation field before the base schema sees it."""
    if type(value) is not dict or FIELD not in value:
        return value, None
    display = value[FIELD]
    kind = value.get('kind', 'plot')
    if (type(kind) is not str or version < MINIMUM_VERSION.get(kind, 7)
            or kind == 'fill' and value.get('mask') == 'hline'
            or not (type(display) is str and display in ('all', 'none')
                    or version >= 5 and kind == 'plot' and valid_destinations(display))):
        raise ValueError('Invalid visual display metadata.')
    return {key: item for key, item in value.items() if key != FIELD}, (
        display if type(display) is str else list(display))
