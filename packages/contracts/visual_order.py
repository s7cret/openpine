"""Declaration options for placing chart visuals in depth."""

from dataclasses import dataclass
from . import drawing_limits

DEFAULTS = {'explicit_plot_zorder': False, 'behind_chart': True}
FIELDS = tuple(DEFAULTS)
SUFFIX = '.visual-order'
OVERLAY_SUFFIX = '.visual-order-overlay'


def available(operation, version):
    from .trading import STRATEGY_DECLARATIONS

    if version >= 4 and (operation == 'harness.indicator' or operation in STRATEGY_DECLARATIONS):
        return FIELDS if version >= 5 else FIELDS[:1]
    return ()


def fields(overload_id):
    overload_id = drawing_limits.base_id(overload_id)
    if overload_id.endswith(OVERLAY_SUFFIX):
        return FIELDS
    if overload_id.endswith(SUFFIX):
        return FIELDS[:1]
    return ()


def base_id(overload_id):
    return drawing_limits.base_id(overload_id).removesuffix(OVERLAY_SUFFIX).removesuffix(SUFFIX)


def arguments(call):
    names = fields(call.overload_id)
    end = len(call.arguments) - len(drawing_limits.fields(call.overload_id))
    return zip(names, call.arguments[end - len(names):end], strict=True)


@dataclass(frozen=True)
class VisualOrder:
    explicit_plot_zorder: bool
    behind_chart: bool | None
