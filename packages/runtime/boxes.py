"""Box records owned by a confirmed evaluation or preview branch."""
from packages.contracts.box_borders import COORDINATE_CONSTRUCTOR, EXTEND_TOKENS, SETTERS, STYLE_TOKENS
from packages.contracts.boxes import COORDINATES, validate_box
from packages.contracts.line_styles import DEFAULT_STYLE, DEFAULT_WIDTH, MAX_WIDTH, STYLE_PREFIX, valid_width
from .drawings import Drawings, coordinate_mode
from .series import KernelError


def border_value(name, value):
    if name == 'border_style':
        if value in STYLE_TOKENS:
            return value.removeprefix(STYLE_PREFIX)
        raise KernelError('BOX_BORDER_STYLE', 'Expected a solid, dotted or dashed border style.', 'rule.boxes')
    if name == 'extend':
        if value in EXTEND_TOKENS:
            return value.removeprefix('extend.')
        raise KernelError('BOX_EXTEND', 'Expected extend.none, extend.left, extend.right or extend.both.', 'rule.boxes')
    if not valid_width(value):
        raise KernelError('BOX_BORDER_WIDTH', f'Box border width must be an integer in 1..{MAX_WIDTH}.', 'rule.boxes')
    return value


class Boxes(Drawings):
    kind = 'box'
    fields = (*COORDINATES, 'border_color', 'bgcolor')
    updates = {'set_lefttop': ('left', 'top'), 'set_rightbottom': ('right', 'bottom'),
               'set_border_color': ('border_color',), 'set_bgcolor': ('bgcolor',)}
    validate = staticmethod(validate_box)
    invalid_record = 'Box coordinates require bar indices and finite prices or na, with RGBA colors or na.'

    def apply(self, operation, arguments):
        if operation == COORDINATE_CONSTRUCTOR:
            *coordinates, border, width, style, extend, xloc, background = arguments
            return self.create(dict(zip(COORDINATES, coordinates, strict=True), border_color=border, bgcolor=background,
                xloc=coordinate_mode(xloc), border_width=border_value('border_width', width),
                border_style=border_value('border_style', style), extend=border_value('extend', extend)))
        if operation in SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = SETTERS[operation]
                record = dict(xloc='bar_index', border_width=DEFAULT_WIDTH, border_style=DEFAULT_STYLE, extend='none')
                record.update(self.live[identity])
                record[name] = border_value(name, value)
                self.write('update', record)
            return None
        return super().apply(operation, arguments)
