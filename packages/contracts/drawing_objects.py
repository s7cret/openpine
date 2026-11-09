"""The bounded Pine drawing-copy and inventory surface."""

from dataclasses import replace

from .drawings import BASES, PLURALS, contracts
from .model import ReferenceContract, ValueType
from .wire import digest

COPY_KINDS = {'line.copy': 'line', 'label.copy': 'label', 'box.copy': 'box'}
ALL_KINDS = {kind + '.all': kind for kind in BASES}
ARRAY_TYPES = tuple('array.' + kind for kind in BASES)
READ_OPERATIONS = ('size', 'get')


def is_drawing_array(base):
    return base in ARRAY_TYPES


def inventory_contract(row, kind):
    if row['parameters'] or row['result_type_spellings'] != [f'array<{kind}>']:
        raise ValueError(f'Unexpected pinned type for {row["name"]}.')
    assertion = f'assertion.openpine.drawings.v{row["language_version"]}.{row["name"]}'
    return ReferenceContract(row['symbol_id'], assertion, digest(row), row['name'],
                             ValueType('array.' + kind, 'series'))


def copy_contract(row, kind):
    source_type = f'series {kind}'
    if ([(p['name'], p['type_spellings'], p['requiredness_claim']) for p in row['parameters']]
            != [('id', [source_type], 'required')] or row['result_type_spellings'] != [source_type]):
        raise ValueError(f'Unexpected pinned signature for {row["name"]}.')
    api = dict(id=f'openpine.drawings.v{row["language_version"]}.{row["name"]}', name=row['name'],
               operation=row['name'], parameters=[dict(name='id', types=[kind], qualifier='series')],
               defaults=[], named=True, result=kind, effects=[f'draws_{PLURALS[kind]}'])
    contract = contracts((api,))[api['id']]
    return api, replace(contract, signature_sha256=digest({'api': api, 'reference': row}))
