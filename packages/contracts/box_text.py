"""Versioned text attributes for Pine boxes."""
from . import box_borders, drawings, label_text
from .colors import constants as colors, valid as valid_color
from .label_styles import valid_size
from .wire import digest, read_json

COORDINATE_CONSTRUCTOR = 'box.new_text'
POINT_CONSTRUCTOR = 'box.new_points_text'
CONSTRUCTORS = (COORDINATE_CONSTRUCTOR, POINT_CONSTRUCTOR)
HORIZONTAL = label_text.ALIGNMENTS
VERTICAL = ('top', 'center', 'bottom')
WRAPPING = ('none', 'auto')
TEXT_LIMIT = 4096
OPTIONS = (
    ('text', 'string', 5), ('text_size', 'string', 5), ('text_color', 'color', 5),
    ('text_halign', 'string', 5), ('text_valign', 'string', 5), ('text_wrap', 'string', 5),
    ('text_font_family', 'string', 5), ('force_overlay', 'bool', 5),
    ('text_formatting', label_text.TEXT_FORMAT, 6),
)
VERSIONS = {name: version for name, _, version in OPTIONS}
SETTERS = {f'box.set_{name}': name for name in VERSIONS if name != 'force_overlay'}
FIELDS = frozenset(SETTERS.values())
OPERATIONS = {
    **{operation: (*box_borders.OPERATIONS[base], *((name, kind) for name, kind, _ in OPTIONS))
       for operation, base in zip(CONSTRUCTORS, box_borders.CONSTRUCTORS, strict=True)},
    **{f'box.set_{name}': (('id', 'box'), (name, kind)) for name, kind, _ in OPTIONS if name in FIELDS},
}


def defaults(version):
    return dict(text='', text_size='auto', text_color=colors(version)['color.black'][0],
                text_halign='center', text_valign='center', text_wrap='none',
                text_font_family='default', text_formatting=0)


def tokens(version):
    if version < 5:
        return {}
    names = ('text.align_top', 'text.align_bottom', 'text.wrap_none', 'text.wrap_auto')
    return {name: (name, 'string') for name in names}


def parameter(name, base, version):
    return dict(name=name, types=[base, 'int'] if name == 'text_size' and version == 6 else [base],
                qualifier='const' if name == 'force_overlay' else 'series')


def apis(version):
    if version < 5:
        return []
    prefix = f'openpine.box-text.v{version}.'
    wire_defaults = dict(defaults(version), text_halign='text.align_center', text_valign='text.align_center',
                         text_wrap='text.wrap_none', text_font_family='font.family_default', force_overlay=False)
    options = [parameter(name, base, version) for name, base, since in OPTIONS if version >= since]
    result = []
    for api in box_borders.apis(version):
        if api['operation'] in box_borders.CONSTRUCTORS:
            operation = CONSTRUCTORS[box_borders.CONSTRUCTORS.index(api['operation'])]
            result.append(dict(api, id=prefix + operation, operation=operation,
                               parameters=[*api['parameters'], *options],
                               defaults=[*api['defaults'], *(wire_defaults[p['name']] for p in options)]))
    for operation, name in SETTERS.items():
        if version >= VERSIONS[name]:
            result.append(dict(id=prefix + operation, name=operation, operation=operation,
                               parameters=[parameter(n, base, version) for n, base in OPERATIONS[operation]],
                               defaults=[], named=True, result='void', effects=['draws_boxes']))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def evidence_version(reference, version):
    return dict(apis=apis(version), constants=list(tokens(version)), reference_sha256=digest(reference))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/box-text-profile.json')
    if (profile['schema_version'] != 'openpine.box-text-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != evidence_version(snapshot.references[version], version)):
        raise ValueError('Box text ABI differs from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    box_borders.validate_arguments(node, api)


def valid_text(value):
    return type(value) is str and len(value) <= TEXT_LIMIT


def valid_text_attributes(record):
    return (valid_text(record['text']) and valid_size(record['text_size']) and valid_color(record['text_color'])
            and record['text_halign'] in HORIZONTAL and record['text_valign'] in VERTICAL
            and record['text_wrap'] in WRAPPING and record['text_font_family'] in label_text.FAMILIES
            and label_text.valid_format(record['text_formatting']))
