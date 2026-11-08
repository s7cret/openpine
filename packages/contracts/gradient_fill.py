"""Wire records for per-bar gradients clipped by a pair of visual outputs."""

import math
import re

from .colors import valid as valid_color

SCHEMA = 'openpine.gradient-fill.1'
STYLE = 'gradient'
FIELDS = ('top_value', 'bottom_value', 'top_color', 'bottom_color')
OPERATIONS = {'bands.gradient_plot': 'plot', 'bands.gradient_hline': 'hline'}


def validate_sample(sample):
    if type(sample) is not dict or sample.keys() != set(FIELDS):
        raise ValueError('Invalid gradient stops.')
    for name in FIELDS[:2]:
        value = sample[name]
        if not (value is None or type(value) is int and -2**63<=value<2**63
                or type(value) is float and math.isfinite(value)):
            raise ValueError('Gradient levels must be finite numbers or na.')
    if not all(valid_color(sample[name]) for name in FIELDS[2:]):
        raise ValueError('Gradient colors must be RGBA values or na.')


def descriptor(value, version=6):
    if (version < 5 or type(value) is not dict or value.keys() != {'kind','style','references','fillgaps','mask'}
            or value['kind'] != 'fill' or value['style'] != STYLE or value['mask'] not in OPERATIONS.values()
            or type(value['fillgaps']) is not bool):
        raise ValueError('Invalid gradient fill descriptor.')
    refs = value['references']
    if (type(refs) not in (tuple,list) or len(refs)!=2
            or any(type(ref) is not str or not re.fullmatch(r'call:[0-9]{1,6}:[0-9]{1,6}',ref) for ref in refs)):
        raise ValueError('Invalid gradient fill references.')
    return value


def event(value, operation, version):
    if type(value) is not dict or 'gradient' not in value:
        raise ValueError('Gradient event requires per-bar stops.')
    meta = descriptor({k:v for k,v in value.items() if k!='gradient'},version)
    if OPERATIONS.get(operation) != meta['mask']:
        raise ValueError('Gradient mask differs from its overload.')
    validate_sample(value['gradient'])
    return meta


def metadata(value):
    if type(value) is not dict or value.get('schema_version')!=SCHEMA:
        raise ValueError('Invalid gradient fill schema.')
    meta=descriptor({k:v for k,v in value.items() if k!='schema_version'})
    return {'presentation_schema':SCHEMA,**meta}


def validate_row(row, meta=None):
    if (type(row) is not dict or row.keys()!={'value','gradient'} or row['value'] is not None
            or meta is not None and meta.get('schema_version')!=SCHEMA):
        raise ValueError('Invalid gradient fill row.')
    validate_sample(row['gradient'])
