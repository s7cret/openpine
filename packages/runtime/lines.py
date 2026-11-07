"""Line records owned by a confirmed evaluation or preview branch."""
from fractions import Fraction

from packages.contracts.line_extensions import MODES
from packages.contracts.lines import validate_line
from packages.contracts.line_styles import (
    COORDINATE_CONSTRUCTOR as STYLED_LINE, OVERLAY_COORDINATE_CONSTRUCTOR as OVERLAY_LINE,
    DEFAULT_COLOR, DEFAULT_STYLE, DEFAULT_WIDTH, MAX_WIDTH, SETTERS,
    STYLES, STYLE_PREFIX, valid_width,
)
from packages.runtime.series import KernelError
from .drawings import Drawings, coordinate_mode


def extension(value):
    if isinstance(value, str) and value.startswith('extend.') and value[7:] in MODES:
        return value[7:]
    raise KernelError('LINE_EXTEND', 'Expected extend.none, extend.left, extend.right or extend.both.', 'rule.lines')


def stroke_value(name, value):
    if name == 'style':
        if isinstance(value, str) and value.startswith(STYLE_PREFIX) and value[len(STYLE_PREFIX):] in STYLES:
            return value[len(STYLE_PREFIX):]
        raise KernelError('LINE_STYLE', 'Expected a supported line.style_* constant.', 'rule.lines')
    if name == 'width' and not valid_width(value):
        raise KernelError('LINE_WIDTH', f'Line width must be an integer in 1..{MAX_WIDTH}.', 'rule.lines')
    return value


def price_at(line, x):
    if line is None or x is None:
        return None
    x1, y1, x2, y2 = (line[key] for key in ('x1', 'y1', 'x2', 'y2'))
    if None in (x1, y1, x2, y2) or x1 == x2:
        return None
    if x == x1 or y1 == y2:
        return y1
    if x == x2:
        return y2
    # Keep the bar-index difference exact and round only the final price.
    first = Fraction(y1)
    try:
        return float(first + Fraction(x - x1, x2 - x1) * (Fraction(y2) - first))
    except OverflowError:
        return None


class Lines(Drawings):
    kind = 'line'
    fields = ('x1', 'y1', 'x2', 'y2')
    updates = {'set_xy1': ('x1', 'y1'), 'set_xy2': ('x2', 'y2')}
    validate = staticmethod(validate_line)
    invalid_record = 'Line coordinates require bar indices and finite prices or na.'

    def apply(self, operation, arguments):
        if operation in (STYLED_LINE, OVERLAY_LINE):
            force_overlay = operation == OVERLAY_LINE and arguments[-1]
            if operation == OVERLAY_LINE:
                arguments = arguments[:-1]
            *coordinates, xloc, extend, color, style, width = arguments
            record = dict(zip(self.fields, coordinates, strict=True), xloc=coordinate_mode(xloc), extend=extension(extend),
                          color=color, style=stroke_value('style', style), width=stroke_value('width', width))
            if force_overlay is True:
                record['force_overlay'] = True
            return self.create(record)
        if operation in SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = SETTERS[operation]
                record = dict(xloc='bar_index', extend='none', color=DEFAULT_COLOR,
                              style=DEFAULT_STYLE, width=DEFAULT_WIDTH)
                record.update(self.live[identity])
                record[name] = stroke_value(name, value)
                self.write('update', record)
            return None
        if operation == 'line.new_xloc':
            *coordinates, xloc, extend = arguments
            return self.create(dict(zip(self.fields, coordinates, strict=True),
                                    xloc=coordinate_mode(xloc), extend=extension(extend)))
        if operation == 'line.new_extended':
            *coordinates, mode = arguments
            record = dict(zip(self.fields, coordinates, strict=True), extend=extension(mode))
            return self.create(record)
        if operation == 'line.set_extend':
            identity, mode = arguments
            if identity in self.live:
                self.write('update', {**self.live[identity], 'extend': extension(mode)})
            return None
        if operation == 'line.get_price':
            identity, x = arguments
            line = self.live.get(identity)
            if line is not None and line.get('xloc') == 'bar_time':
                raise KernelError('LINE_PRICE_XLOC', 'line.get_price requires xloc.bar_index.', 'rule.lines')
            return price_at(line, x)
        return super().apply(operation, arguments)
