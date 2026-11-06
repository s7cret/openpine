"""Box records owned by a confirmed evaluation or preview branch."""
from packages.contracts import box_text, label_text
from packages.contracts.box_borders import COORDINATE_CONSTRUCTOR, EXTEND_TOKENS, SETTERS, STYLE_TOKENS
from packages.contracts.boxes import COORDINATES, validate_box
from packages.contracts.colors import valid as valid_color
from packages.contracts.label_styles import valid_size
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


BORDER_DEFAULTS = dict(xloc='bar_index', border_width=DEFAULT_WIDTH, border_style=DEFAULT_STYLE, extend='none')
TEXT_DEFAULTS = box_text.defaults(5)
TEXT_TOKENS = {
    'text_halign': ('text.align_', box_text.HORIZONTAL),
    'text_valign': ('text.align_', box_text.VERTICAL),
    'text_wrap': ('text.wrap_', box_text.WRAPPING),
    'text_font_family': ('font.family_', label_text.FAMILIES),
}
TEXT_VALUES = {
    'text': (box_text.valid_text, f'Text must contain at most {box_text.TEXT_LIMIT} characters.'),
    'text_size': (valid_size, 'Expected a size.* value or a nonnegative integer size.'),
    'text_color': (valid_color, 'Expected an RGBA text color or na.'),
    'text_formatting': (label_text.valid_format, 'Expected a Pine text format.'),
}


def text_value(name, value):
    if name in TEXT_TOKENS:
        prefix, options = TEXT_TOKENS[name]
        if value in tuple(prefix + option for option in options):
            return value.removeprefix(prefix)
        message = 'Expected one of ' + ', '.join(prefix + option for option in options) + '.'
    else:
        valid, message = TEXT_VALUES[name]
        if valid(value):
            return value
    raise KernelError(f'BOX_{name.upper()}', message, 'rule.boxes')


class Boxes(Drawings):
    kind = 'box'
    fields = (*COORDINATES, 'border_color', 'bgcolor')
    updates = {'set_lefttop': ('left', 'top'), 'set_rightbottom': ('right', 'bottom'),
               'set_border_color': ('border_color',), 'set_bgcolor': ('bgcolor',)}
    validate = staticmethod(validate_box)
    invalid_record = 'Box coordinates require bar indices and finite prices or na, with RGBA colors or na.'

    def apply(self, operation, arguments):
        if operation in (COORDINATE_CONSTRUCTOR, box_text.COORDINATE_CONSTRUCTOR):
            left, top, right, bottom, border, width, style, extend, xloc, background, *options = arguments
            record = dict(left=left, top=top, right=right, bottom=bottom, border_color=border, bgcolor=background,
                          xloc=coordinate_mode(xloc), border_width=border_value('border_width', width),
                          border_style=border_value('border_style', style), extend=border_value('extend', extend))
            if operation == box_text.COORDINATE_CONSTRUCTOR:
                record.update(TEXT_DEFAULTS)
                for (name, _, _), value in zip(box_text.OPTIONS, options):
                    if name in box_text.FIELDS:
                        record[name] = text_value(name, value)
            return self.create(record)
        if operation in SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = SETTERS[operation]
                record = {**BORDER_DEFAULTS, **self.live[identity]}
                record[name] = border_value(name, value)
                self.write('update', record)
            return None
        if operation in box_text.SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = box_text.SETTERS[operation]
                record = {**BORDER_DEFAULTS, **TEXT_DEFAULTS, **self.live[identity], name: text_value(name, value)}
                self.write('update', record)
            return None
        return super().apply(operation, arguments)
