"""Emit calculated bars through the same transaction as other plot outputs."""

from packages.contracts.model import ValueType
from packages.contracts.visual_ohlc import DEFAULT_COLOR, PRICES, validate_event
from packages.contracts.visual_panes import WINDOW


def kernel(kind, plot_event, kernel_error):
    operation = 'ohlc.' + kind

    def run(arguments, context):
        version = context.language_version
        prices = arguments[:4]
        title, color, *appearance = arguments[4:]
        forced = version >= 5 and appearance.pop() is True
        extra_colors = 2 if kind == 'plotcandle' and version >= 3 else 0
        window = appearance.pop() if len(appearance)>extra_colors else None
        wick, border = (appearance or (DEFAULT_COLOR, DEFAULT_COLOR)) if kind == 'plotcandle' else (None, None)
        sample = None
        try:
            if all(price is not None for price in prices):
                opening, high, low, closing = map(float, prices)
                bounds = (opening, high, low, closing)
                sample = dict(zip(PRICES, (opening, max(bounds), min(bounds), closing), strict=True),
                              color=color, wickcolor=wick, bordercolor=border)
            event = {'kind': kind, 'bar': sample}
            if forced:
                event['force_overlay'] = True
            if window is not None:
                event[WINDOW] = window
            validate_event(event, operation, version)
        except (ValueError, OverflowError) as error:
            raise kernel_error('VISUAL_OHLC', str(error), 'rule.visual-ohlc') from error
        return plot_event(None, title, event, output_type=ValueType('float', 'series'))

    return run
