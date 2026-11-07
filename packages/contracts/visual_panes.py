"""Additive placement for bar-aligned visual outputs."""

FLAG = 'force_overlay'
ABI_SUFFIX = '.overlay'
SOURCE_NAMES = ('plot', 'bgcolor', 'plotshape', 'plotchar', 'plotarrow')


def overlay_api(api):
    return dict(api, id=api['id'] + ABI_SUFFIX,
                parameters=[*api['parameters'], dict(name=FLAG, types=['bool'], qualifier='const')],
                defaults=[*api['defaults'], False])


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
