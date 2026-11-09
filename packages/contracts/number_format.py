"""Closed formatting metadata and additive argument boundaries for visuals."""

from dataclasses import dataclass, replace

PLOT_FORMATS = ('price', 'percent', 'volume')
DECLARATION_FORMATS = ('inherit', *PLOT_FORMATS)
FIELDS = ('format', 'precision')
DEFAULTS = {'format': '', 'precision': -1}
TYPES = {'format': 'string', 'precision': 'int'}
SUFFIX = '.number-format'
PRECISION_SUFFIX = '.number-precision'


def available(operation, version):
    from .trading import STRATEGY_DECLARATIONS
    from .visual_display import DESTINATION_OPERATIONS

    if operation == 'harness.indicator' or operation in STRATEGY_DECLARATIONS and version >= 3:
        return FIELDS if version >= 4 else ('precision',)
    if operation in DESTINATION_OPERATIONS and version >= 5:
        return FIELDS
    return ()


def fields(overload_id):
    if overload_id.endswith(SUFFIX):
        return FIELDS
    if overload_id.endswith(PRECISION_SUFFIX):
        return ('precision',)
    return ()


def core_id(overload_id):
    return overload_id.removesuffix(SUFFIX).removesuffix(PRECISION_SUFFIX)


def core_arguments(call):
    return call.arguments[:len(call.arguments) - len(fields(call.overload_id))]


def core_call(call):
    if not fields(call.overload_id):
        return call
    return replace(call, overload_id=core_id(call.overload_id), arguments=core_arguments(call))


def check_value(name, value, *, declaration):
    if name == 'format':
        if type(value) is not str or value not in (DECLARATION_FORMATS if declaration else PLOT_FORMATS):
            raise ValueError('Unsupported Pine numeric format.')
    elif type(value) is not int or not 0 <= value <= 16:
        raise ValueError('Pine display precision requires an integer from 0 to 16.')


@dataclass(frozen=True)
class NumberFormat:
    format: str | None = None
    precision: int | None = None

    def __post_init__(self):
        if self.format is None and self.precision is None:
            raise ValueError('Expected at least one numeric formatting option.')
        for name in FIELDS:
            value = getattr(self, name)
            if value is not None:
                check_value(name, value, declaration=True)


@dataclass(frozen=True)
class PlotNumberFormat:
    output_id: str
    style: NumberFormat

    def __post_init__(self):
        if not self.output_id or self.style.format == 'inherit':
            raise ValueError('Invalid plot formatting metadata.')


@dataclass(frozen=True)
class NumberFormatting:
    declaration: NumberFormat | None
    plots: tuple[PlotNumberFormat, ...]

    def __post_init__(self):
        if self.declaration is None and not self.plots:
            raise ValueError('Missing numeric formatting metadata.')
        if len({row.output_id for row in self.plots}) != len(self.plots):
            raise ValueError('Repeated plot formatting output ID.')


def instrument_precision(mintick):
    """Read the decimal places of pinned instrument metadata, when available."""
    if type(mintick) is str:
        _, point, fraction = mintick.partition('.')
        return min(16, len(fraction.rstrip('0'))) if point else 0
    return None
