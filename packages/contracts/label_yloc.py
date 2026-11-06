"""Additive label contracts for vertical coordinate modes."""
from . import drawings, label_styles
from .wire import digest, read_json

COORDINATE_CONSTRUCTOR = 'label.new_yloc'
POINT_CONSTRUCTOR = 'label.new_points_yloc'
CONSTRUCTORS = (COORDINATE_CONSTRUCTOR, POINT_CONSTRUCTOR)
SET_MODE = 'label.set_yloc'
SET_PRICE = 'label.set_y'
MODES = tuple(token.removeprefix('yloc.') for token in label_styles.LOCATIONS)
OPERATIONS = {
    COORDINATE_CONSTRUCTOR: label_styles.OPERATIONS[label_styles.COORDINATE_CONSTRUCTOR],
    POINT_CONSTRUCTOR: label_styles.OPERATIONS[label_styles.POINT_CONSTRUCTOR],
    SET_MODE: (('id', 'label'), ('yloc', 'string')),
    SET_PRICE: (('id', 'label'), ('y', 'float')),
}


def apis(version):
    if version < 4:
        return []
    result = []
    prefix = f'openpine.label-yloc.v{version}.'
    for old in label_styles.apis(version):
        if old['operation'] not in label_styles.CONSTRUCTORS:
            continue
        operation = CONSTRUCTORS[label_styles.CONSTRUCTORS.index(old['operation'])]
        parameters = [{**p, 'qualifier': 'series'} if p['name'] == 'yloc' else p for p in old['parameters']]
        result.append(dict(old, id=prefix + operation, operation=operation, parameters=parameters))
    for operation in (SET_MODE, SET_PRICE):
        result.append(dict(id=prefix + operation, name=operation, operation=operation,
                           parameters=[dict(name=name, types=[base], qualifier='series')
                                       for name, base in OPERATIONS[operation]],
                           defaults=[], named=True, result='void', effects=['draws_labels']))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def evidence_version(reference, version):
    return dict(apis=apis(version), locations=list(label_styles.LOCATIONS) if version >= 4 else [],
                reference_sha256=digest(reference))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/label-yloc-profile.json')
    if (profile['schema_version'] != 'openpine.label-yloc-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != evidence_version(snapshot.references[version], version)):
        raise ValueError('Label coordinate modes differ from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    label_styles.validate_arguments(node, api)
