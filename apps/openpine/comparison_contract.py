"""Application diagnostic protocol; it is separate from the Pine computation ABI."""
from packages.storage.diagnostics import identifier

SCHEMA='openpine.comparison.1'
def request(value):
    if type(value) is not dict or not {'request_id','left','right'}<=value.keys()<={'request_id','left','right','mode'}:raise ValueError('Provide request_id, left and right, optionally mode.')
    if value.get('mode','compatible') not in ('compatible','data_only'):raise ValueError('Comparison mode must be compatible or data_only.')
    identifier(value['request_id'])
    for side in ('left','right'):
        item=value[side]
        if type(item) is not dict or item.keys()!={'kind','id'} or item['kind'] not in ('job','replay'):raise ValueError('Comparison sides require kind job/replay and id.')
        identifier(item['id'])
    return {**value,'mode':value.get('mode','compatible')}
