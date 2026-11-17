"""Pine chart boundary references and their observed-history fence."""

from .model import ReferenceIR


LAST_TIME = 'last_bar_time'
LAST_INDEX = 'last_bar_index'
TRADING_DAY = 'time_tradingday'
VERSIONS = {LAST_TIME: 5, LAST_INDEX: 5, TRADING_DAY: 4}
SLOTS = {name: 'environment.' + name for name in VERSIONS}
END_SLOTS = (SLOTS[LAST_TIME], SLOTS[LAST_INDEX])


def uses_chart_end(program):
    from .context_ir import ir_nodes

    return any(isinstance(node, ReferenceIR) and node.contract.slot in END_SLOTS
               for node in ir_nodes(program.statements))


def validate_history_position(environment, timestamp, index):
    """The recorded candle at the declared index must open at the declared time."""
    end_index = environment.history_last_index
    if end_index is None:
        return
    end_time = environment.history_last_ms
    if (timestamp > end_time) != (index > end_index) or (timestamp == end_time) != (index == end_index):
        raise ValueError('Replay candle cursor differs from the pinned historical end time/index.')
