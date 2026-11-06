"""Versioned label text attributes and the v6 text-format token."""
from . import drawings, label_styles, label_yloc
from .model import LiteralIR
from .wire import digest, read_json

COORDINATE_CONSTRUCTOR = 'label.new_text'
POINT_CONSTRUCTOR = 'label.new_points_text'
CONSTRUCTORS = (COORDINATE_CONSTRUCTOR, POINT_CONSTRUCTOR)
TEXT_FORMAT = 'text_format'
TOOLTIP_LIMIT = 4096
ALIGNMENTS = ('left', 'center', 'right')
FAMILIES = ('default', 'monospace')
FORMATS = {'text.format_none': 0, 'text.format_bold': 1, 'text.format_italic': 2}
OPTIONS = (('textalign', 'string', 4, 'text.align_center'), ('tooltip', 'string', 4, ''),
           ('text_font_family', 'string', 5, 'font.family_default'), ('force_overlay', 'bool', 5, False),
           ('text_formatting', TEXT_FORMAT, 6, 0))
DEFAULTS = dict(textalign='center', tooltip='', text_font_family='default', text_formatting=0)
SETTERS = {f'label.set_{name}': name for name in DEFAULTS}
VERSIONS = {name: version for name, _, version, _ in OPTIONS}
OPERATIONS = {
    **{op: (*label_yloc.OPERATIONS[old], *((name, base) for name, base, _, _ in OPTIONS))
       for op, old in zip(CONSTRUCTORS, label_yloc.CONSTRUCTORS, strict=True)},
    **{f'label.set_{name}': (('id', 'label'), (name, base)) for name, base, _, _ in OPTIONS if name in DEFAULTS},
}


def constants(version):
    tokens = {}
    if version >= 4:
        tokens.update({f'text.align_{value}': (f'text.align_{value}', 'string') for value in ALIGNMENTS})
    if version >= 5:
        tokens.update({f'font.family_{value}': (f'font.family_{value}', 'string') for value in FAMILIES})
    if version >= 6:
        tokens.update({name: (value, TEXT_FORMAT) for name, value in FORMATS.items()})
    return tokens


def apis(version):
    result = []
    prefix = f'openpine.label-text.v{version}.'
    extra = [(name, base, default) for name, base, since, default in OPTIONS if version >= since]
    for old in label_yloc.apis(version):
        if old['operation'] not in label_yloc.CONSTRUCTORS:
            continue
        operation = CONSTRUCTORS[label_yloc.CONSTRUCTORS.index(old['operation'])]
        parameters = [*old['parameters'], *(dict(name=name, types=[base],
            qualifier='const' if name == 'force_overlay' else 'series') for name, base, _ in extra)]
        result.append(dict(old, id=prefix + operation, operation=operation, parameters=parameters,
                           defaults=[*old['defaults'], *(value for _, _, value in extra)]))
    for operation, name in SETTERS.items():
        if version >= VERSIONS[name]:
            result.append(dict(id=prefix + operation, name=operation, operation=operation,
                               parameters=[dict(name=arg, types=[base], qualifier='series')
                                           for arg, base in OPERATIONS[operation]],
                               defaults=[0] if name == 'text_formatting' else [],
                               named=True, result='void', effects=['draws_labels']))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def evidence_version(reference, version):
    return dict(apis=apis(version), constants={name: list(value) for name, value in constants(version).items()},
                reference_sha256=digest(reference))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/label-text-profile.json')
    if (profile['schema_version'] != 'openpine.label-text-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != evidence_version(snapshot.references[version], version)):
        raise ValueError('Label text attributes differ from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    label_styles.validate_arguments(node, api)
    for argument, parameter in zip(node.arguments, api['parameters'], strict=True):
        if parameter['name'] == 'force_overlay' and (
                type(argument.expression) is not LiteralIR or argument.expression.value is not False):
            raise ValueError('The label text profile requires force_overlay=false.')


def valid_format(value):
    return type(value) is int and 0 <= value <= 3


def valid_tooltip(value):
    return type(value) is str and len(value) <= TOOLTIP_LIMIT


def valid_text_attributes(record):
    align = record['textalign']
    return ((align in ALIGNMENTS or align is None and 'style' not in record) and valid_tooltip(record['tooltip'])
            and record['text_font_family'] in FAMILIES and valid_format(record['text_formatting']))
