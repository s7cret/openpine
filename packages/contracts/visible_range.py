"""Opening timestamps of the first and last visible candles of the primary chart."""

from .calendar import MAX_TIME
from .model import ReferenceIR

SINCE = 5
FIELDS = {'chart.left_visible_bar_time': 'left_ms', 'chart.right_visible_bar_time': 'right_ms'}
SLOTS = {name: 'environment.' + name for name in FIELDS}


def validate_visible_range(value):
    if (type(value) is not dict or value.keys() != {'left_ms', 'right_ms'}
            or any(type(t) is not int for t in value.values())
            or not 0 <= value['left_ms'] <= value['right_ms'] < MAX_TIME):
        raise ValueError('Visible range requires ordered left_ms and right_ms Unix millisecond timestamps.')


def uses_visible_range(program):
    if program.identity.language_version < SINCE:
        return False
    from .context_ir import ir_nodes

    return any(isinstance(node, ReferenceIR) and node.contract.slot in SLOTS.values()
               for node in ir_nodes((program.statements, program.functions)))
