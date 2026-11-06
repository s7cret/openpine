"""Label records owned by a confirmed evaluation or preview branch."""
from packages.contracts.label_styles import (
    COORDINATE_CONSTRUCTOR, DEFAULT_COLOR, DEFAULT_SIZE, DEFAULT_STYLE, DEFAULT_TEXTCOLOR,
    LOCATIONS, PRICE, SETTERS, STYLES, valid_size,
)
from packages.contracts.label_yloc import COORDINATE_CONSTRUCTOR as VERTICAL_CONSTRUCTOR, SET_MODE
from packages.contracts import label_text
from packages.contracts.labels import TEXT_LIMIT, validate_label
from packages.contracts.model import (
    LabelRecord, LocatedLabelRecord, OverlayLabelRecord, StyledLabelRecord, StyledTextLabelRecord,
    StyledVerticalLabelRecord, TextLabelRecord, VerticalLabelRecord,
)
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


def vertical_mode(value):
    if value in LOCATIONS:
        return value.removeprefix('yloc.')
    raise KernelError('LABEL_YLOC', 'Expected yloc.price, yloc.abovebar or yloc.belowbar.', 'rule.labels')


def text_value(name, value):
    if name == 'textalign' and value in tuple('text.align_' + mode for mode in label_text.ALIGNMENTS):
        return value.removeprefix('text.align_')
    if name == 'text_font_family' and value in tuple('font.family_' + mode for mode in label_text.FAMILIES):
        return value.removeprefix('font.family_')
    if name == 'tooltip' and label_text.valid_tooltip(value) or name == 'text_formatting' and label_text.valid_format(value):
        return value
    raise KernelError('LABEL_' + name.upper(), f'Invalid label {name}.', 'rule.labels')


class Labels(Drawings):
    kind = 'label'
    fields = ('x', 'y', 'text')
    updates = {'set_xy': ('x', 'y'), 'set_y': ('y',), 'set_text': ('text',)}
    validate = staticmethod(validate_label)
    invalid_record = f'Label text requires a string up to {TEXT_LIMIT} characters.'

    def result_records(self):
        result = []
        for label in self.live.values():
            if 'force_overlay' in label:
                record_type = OverlayLabelRecord
            elif 'textalign' in label:
                record_type = StyledTextLabelRecord if 'style' in label else TextLabelRecord
            elif 'yloc' in label:
                record_type = StyledVerticalLabelRecord if 'style' in label else VerticalLabelRecord
            elif 'style' in label:
                record_type = StyledLabelRecord
            else:
                record_type = LocatedLabelRecord if 'xloc' in label else LabelRecord
            result.append(record_type(**label))
        return tuple(result)

    def apply(self, operation, arguments):
        if operation in (COORDINATE_CONSTRUCTOR, VERTICAL_CONSTRUCTOR, label_text.COORDINATE_CONSTRUCTOR):
            x, y, text, xloc, yloc, color, style, textcolor, size, *text_options = arguments
            if operation == COORDINATE_CONSTRUCTOR and yloc != PRICE:
                raise KernelError('LABEL_YLOC', 'Label appearance requires yloc.price.', 'rule.labels')
            record = dict(x=x, y=y, text=text, xloc=coordinate_mode(xloc), color=color,
                          style=appearance_value('style', style), textcolor=textcolor, size=appearance_value('size', size))
            if operation != COORDINATE_CONSTRUCTOR:
                record['yloc'] = vertical_mode(yloc)
            if operation == label_text.COORDINATE_CONSTRUCTOR:
                record.update(label_text.DEFAULTS)
                for (name, _, _, _), value in zip(label_text.OPTIONS, text_options):
                    if name == 'force_overlay':
                        if value is True:
                            record[name] = True
                    else:
                        record[name] = text_value(name, value)
            return self.create(record)
        if operation in label_text.SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = label_text.SETTERS[operation]
                record = dict(xloc='bar_index', yloc='price', **label_text.DEFAULTS)
                if 'style' not in self.live[identity]:
                    record['textalign'] = None
                record.update(self.live[identity])
                record[name] = text_value(name, value)
                self.write('update', record)
            return None
        if operation == SET_MODE:
            identity, value = arguments
            if identity in self.live:
                record = {'xloc': 'bar_index', **self.live[identity], 'yloc': vertical_mode(value)}
                self.write('update', record)
            return None
        if operation in SETTERS:
            identity, value = arguments
            if identity in self.live:
                name = SETTERS[operation]
                record = dict(xloc='bar_index', color=DEFAULT_COLOR, style=STYLES[DEFAULT_STYLE],
                              textcolor=DEFAULT_TEXTCOLOR, size=DEFAULT_SIZE)
                record.update(self.live[identity])
                record[name] = appearance_value(name, value)
                if 'textalign' in record and record['textalign'] is None:
                    record['textalign'] = label_text.DEFAULTS['textalign']
                self.write('update', record)
            return None
        return super().apply(operation, arguments)
