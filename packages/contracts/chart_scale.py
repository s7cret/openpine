"""Closed presentation choices carried by explicitly scaled declarations."""

from typing import Literal

ChartScale = Literal['left', 'right', 'none']
SCALES = ('left', 'right', 'none')
TOKENS = {'scale.' + name: name for name in SCALES}
TYPE = 'scale_type'
SUFFIX = '.chart-scale'


def available(operation, version):
    from .trading import STRATEGY_DECLARATIONS

    return operation == 'harness.indicator' or (operation in STRATEGY_DECLARATIONS and version >= 3)


def present(overload_id):
    return overload_id.endswith(SUFFIX)


def validate(scale, overlay):
    if type(scale) is not str or scale not in SCALES:
        raise ValueError('Use scale.left, scale.right, or scale.none.')
    if scale == 'none' and overlay is not True:
        raise ValueError('scale.none requires overlay=true.')
