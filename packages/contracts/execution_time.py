"""Inputs for the versioned Pine execution clock."""

from .calendar import MAX_TIME
from .model import ReferenceIR


TIMENOW = 'timenow'
SINCE = 3
SLOT = 'environment.' + TIMENOW
SPELLINGS = {3: 'series', 4: 'series[integer]', 5: 'series int', 6: 'series int'}


def validate_execution_time(value):
    if type(value) is not int or not 0 <= value < MAX_TIME:
        raise ValueError('Execution time must be an integer Unix millisecond in the supported UTC calendar.')


def uses_execution_time(program):
    from .context_ir import ir_nodes

    return any(isinstance(node, ReferenceIR) and node.contract.slot == SLOT
               for node in ir_nodes((program.statements, program.functions)))
