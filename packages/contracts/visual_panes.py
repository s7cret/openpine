"""Additive placement for bar-aligned visual outputs."""

FLAG = 'force_overlay'
ABI_SUFFIX = '.overlay'
WINDOW = 'show_last'
WINDOW_SUFFIX = '.window'
OFFSET = 'offset'
OFFSET_LIMIT = 10000
VIEW_FIELDS = (OFFSET, WINDOW)
SOURCE_NAMES = ('plot', 'bgcolor', 'plotshape', 'plotchar', 'plotarrow')


def overlay_api(api):
    return dict(api, id=api['id'] + ABI_SUFFIX,
                parameters=[*api['parameters'], dict(name=FLAG, types=['bool'], qualifier='const')],
                defaults=[*api['defaults'], False])


def window_api(api, version, *, shifted=False):
    parameters = [dict(name=WINDOW, types=['int'], qualifier='const' if version == 3 else 'input')]
    defaults = [None]
    if shifted:
        parameters.insert(0, dict(name=OFFSET, types=['int'], qualifier='input'))
        defaults.insert(0, 0)
    return dict(api, id=api['id'] + WINDOW_SUFFIX,
                parameters=[*api['parameters'], *parameters], defaults=[*api['defaults'], *defaults])


def validate_window(value, version, *, allowed=True, nullable=False):
    if WINDOW in value:
        last = value[WINDOW]
        if (version < 3 or not allowed
                or not (last is None and nullable or type(last) is int and 0 <= last < 2**63)):
            raise ValueError('Invalid show_last value.')


def validate_plot_view(value, version):
    if OFFSET in value or WINDOW in value:
        offset = value.get(OFFSET)
        if WINDOW not in value or type(offset) is not int or abs(offset) > OFFSET_LIMIT:
            raise ValueError('Invalid plot offset or window fields.')
        validate_window(value, version, nullable=True)


def validate_call_view(value, node, *, shifted=False):
    from .model import LiteralIR, NAIR
    fields = VIEW_FIELDS if shifted else (WINDOW,)
    params = {arg.parameter_id.rsplit('.', 1)[-1]: arg.expression for arg in node.arguments}
    for field in fields:
        expr = params.get(field)
        if expr is None:
            valid = field not in value
        elif isinstance(expr, NAIR):
            valid = value.get(field) is None and (field in value) == shifted
        elif isinstance(expr, LiteralIR):
            valid = field in value and value[field] == expr.value
        else:
            valid = not shifted or field in value
        if not valid:
            raise ValueError('Visual window or offset differs from its call.')


def matches_default(expression, expected):
    from .model import LiteralIR, NAIR, ValueType
    if expected is None:
        return type(expression) is NAIR and expression.value_type == ValueType('int')
    return type(expression) is LiteralIR and type(expression.value) is type(expected) and expression.value == expected


def placement(value, version, *, allowed=True):
    if FLAG not in value:
        return {}
    if version < 5 or not allowed or value[FLAG] is not True:
        raise ValueError('Invalid force_overlay presentation.')
    return {FLAG: True}


def validate_call_placement(value, node):
    from .model import LiteralIR
    arg = node.arguments[-1].expression if node.overload_id.endswith(ABI_SUFFIX) else None
    actual = FLAG in value
    if arg is None and actual or isinstance(arg, LiteralIR) and actual != (arg.value is True):
        raise ValueError('Visual placement differs from its call contract.')
