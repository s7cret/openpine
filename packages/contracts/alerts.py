"""Versioned contracts and shared limits for local Pine alert events."""
from .context_ir import ir_nodes
from .model import CallIR, LiteralIR
from .strategy_cycle import MAX_PASSES
from .trading import STRATEGY_DECLARATIONS

CALL = 'alerts.call'
CONDITION = 'alerts.condition'
OPERATIONS = (CALL, CONDITION)
VERSIONS = {'alertcondition': 2, 'alert': 4}
ONCE = 'alert.freq_once_per_bar'
CLOSE = 'alert.freq_once_per_bar_close'
ALL = 'alert.freq_all'
FREQUENCIES = (ONCE, CLOSE, ALL)
DEFAULTS = {CALL: (ONCE,), CONDITION: ('Alert', '')}
MAX_MESSAGE_BYTES = 4096
MAX_PER_PASS = 64
MAX_PER_EVENT = MAX_PER_PASS * MAX_PASSES
PAGE = 100


def nodes(statements):
    return tuple(node for node in ir_nodes(statements)
                 if isinstance(node, CallIR) and node.operation in OPERATIONS)


def definitions(program):
    if not program.statements or program.statements[0].operation in STRATEGY_DECLARATIONS:
        return []
    return [{'call_site_id': node.call_site_id,
             'title': node.arguments[1].expression.value,
             'message': node.arguments[2].expression.value}
            for node in program.statements if isinstance(node, CallIR) and node.operation == CONDITION]


def valid_message(text):
    if type(text) is not str:
        return False
    try:
        return len(text.encode('utf-8')) <= MAX_MESSAGE_BYTES
    except UnicodeEncodeError:
        return False


def validate_call(node):
    expected = DEFAULTS[node.operation]
    for i, arg in enumerate(node.arguments):
        expr = arg.expression
        if arg.origin == 'default' and (i == 0 or not isinstance(expr, LiteralIR) or expr.value != expected[i - 1]):
            raise ValueError('Alert defaults differ from their contract.')
        if node.operation == CONDITION and i > 0 and (
                not isinstance(expr, LiteralIR) or not valid_message(expr.value)):
            raise ValueError('Alert titles and templates require bounded compile-time strings.')
        if node.operation == CALL and i == 1 and isinstance(expr, LiteralIR) and expr.value not in FREQUENCIES:
            raise ValueError('Unknown alert frequency.')
