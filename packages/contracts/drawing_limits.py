"""Declaration options for the live capacities of journaled drawings."""

DEFAULT = 50
KINDS = {
    'max_lines_count': 'lines',
    'max_labels_count': 'labels',
    'max_boxes_count': 'boxes',
    'max_polylines_count': 'polylines',
}
FIELDS = tuple(KINDS)
UPPER = {'lines': 500, 'labels': 500, 'boxes': 500, 'polylines': 100}
SUFFIX = '.drawing-limits'
POLYLINE_SUFFIX = '.drawing-limits-polyline'


def available(operation, version):
    from .trading import STRATEGY_DECLARATIONS

    if version >= 4 and (operation == 'harness.indicator' or operation in STRATEGY_DECLARATIONS):
        return FIELDS if version >= 5 else FIELDS[:-1]
    return ()


def fields(overload_id):
    if overload_id.endswith(POLYLINE_SUFFIX):
        return FIELDS
    if overload_id.endswith(SUFFIX):
        return FIELDS[:-1]
    return ()


def base_id(overload_id):
    return overload_id.removesuffix(POLYLINE_SUFFIX).removesuffix(SUFFIX)


def arguments(call):
    names = fields(call.overload_id)
    return zip(names, call.arguments[len(call.arguments) - len(names):], strict=True)


def validate(name, value):
    upper = UPPER[KINDS[name]]
    if type(value) is not int or not 1 <= value <= upper:
        raise ValueError(f'{name} requires a constant integer from 1 to {upper}.')
