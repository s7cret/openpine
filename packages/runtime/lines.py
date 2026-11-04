"""Line records owned by a confirmed evaluation or preview branch."""
from fractions import Fraction

from packages.contracts.line_extensions import MODES
from packages.contracts.lines import validate_line
from packages.runtime.series import KernelError
from .drawings import Drawings, coordinate_mode


def extension(value):
    if isinstance(value, str) and value.startswith('extend.') and value[7:] in MODES:
        return value[7:]
    raise KernelError('LINE_EXTEND', 'Expected extend.none, extend.left, extend.right or extend.both.', 'rule.lines')


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
