"""Label records owned by a confirmed evaluation or preview branch."""
from packages.contracts.label_styles import (
    COORDINATE_CONSTRUCTOR, DEFAULT_COLOR, DEFAULT_SIZE, DEFAULT_STYLE, DEFAULT_TEXTCOLOR,
    PRICE, SETTERS, STYLES, valid_size,
)
from packages.contracts.labels import TEXT_LIMIT, validate_label
from packages.contracts.model import LabelRecord, LocatedLabelRecord, StyledLabelRecord
from .drawings import Drawings, coordinate_mode
from .series import KernelError


def appearance_value(name, value):
    if name == 'style':
        if isinstance(value, str) and value in STYLES:
            return STYLES[value]
        raise KernelError('LABEL_STYLE', 'Expected one of the six supported label styles.', 'rule.labels')
    if name == 'size' and not valid_size(value):
        raise KernelError('LABEL_SIZE', 'Expected a size token or a nonnegative integer in Pine v6.', 'rule.labels')
    return value


class Labels(Drawings):
    kind = 'label'
    fields = ('x', 'y', 'text')
    updates = {'set_xy': ('x', 'y'), 'set_text': ('text',)}
    validate = staticmethod(validate_label)
    invalid_record = f'Label text requires a string up to {TEXT_LIMIT} characters.'

    def result_records(self):
        return tuple((StyledLabelRecord if 'style' in label else LocatedLabelRecord if 'xloc' in label
                      else LabelRecord)(**label) for label in self.live.values())

    def apply(self, operation, arguments):
        if operation == COORDINATE_CONSTRUCTOR:
            x, y, label_text, xloc, yloc, color, style, textcolor, size = arguments
            if yloc != PRICE:
                raise KernelError('LABEL_YLOC', 'Label appearance requires yloc.price.', 'rule.labels')
            return self.create(dict(x=x, y=y, text=label_text, xloc=coordinate_mode(xloc),
                                    color=color, style=appearance_value('style', style), textcolor=textcolor,
                                    size=appearance_value('size', size)))
        if operation in SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = SETTERS[operation]
                record = dict(xloc='bar_index', color=DEFAULT_COLOR, style=STYLES[DEFAULT_STYLE],
                              textcolor=DEFAULT_TEXTCOLOR, size=DEFAULT_SIZE)
                record.update(self.live[identity])
                record[name] = appearance_value(name, value)
                self.write('update', record)
            return None
        return super().apply(operation, arguments)
