"""Version and expression boundaries for local strategy fill alerts."""
from .alerts import valid_message
from .context_ir import ir_nodes
from .model import BinaryIR, CallIR, ConditionalIR, ConvertIR, LiteralIR, NAIR, ReferenceIR, UnaryIR
from .trading import STRATEGY_DECLARATIONS


PROFILE = 'openpine.strategy-fill-alerts.1'
MESSAGE = 'alert_message'
DISABLE = 'disable_alert'
DEFAULTS = {MESSAGE: '', DISABLE: False}
TYPES = {MESSAGE: 'string', DISABLE: 'bool'}
SUFFIXES = {1: '+fill-message', 2: '+fill-alerts'}
OPERATIONS = ('simulation.entry', 'simulation.entry_pending', 'simulation.entry_group',
              'simulation.close', 'simulation.close_partial')
FRAME_SLOTS = frozenset(('open', 'high', 'low', 'close', 'volume', 'bar_index', 'time'))
CONVERSIONS = ('scalar.tostring', 'scalar.tostring.format')


def available(version):
    return () if version < 4 else (MESSAGE,) if version == 4 else (MESSAGE, DISABLE)


def count(node):
    return next((size for size, suffix in SUFFIXES.items() if node.overload_id.endswith(suffix)), 0)


def configuration(program, requested):
    if type(requested) is not bool:
        raise ValueError('order_fill_alerts requires a boolean.')
    if requested and (not program.statements or program.statements[0].operation not in STRATEGY_DECLARATIONS):
        raise ValueError('Order fill alerts require a strategy.')
    return requested or any(isinstance(node, CallIR) and node.operation in OPERATIONS and count(node)
                            for node in ir_nodes(program.statements))


def validate(node, version):
    size = count(node)
    if not size:
        return
    names = available(version)
    if node.operation not in OPERATIONS or len(names) != size:
        raise ValueError('Order fill alert arguments differ from their version profile.')
    for name, argument in zip(names, node.arguments[-size:], strict=True):
        expr = argument.expression
        if argument.origin == 'default' and (not isinstance(expr, LiteralIR)
                or type(expr.value) is not type(DEFAULTS[name]) or expr.value != DEFAULTS[name]):
            raise ValueError('Order fill alert default differs from its contract.')
        if name == MESSAGE:
            validate_message(expr)


def validate_message(expression):
    remaining = 256
    pending = [(expression, 1)]
    while pending:
        node, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise ValueError('Order fill message exceeds its expression budget.')
        match node:
            case LiteralIR(value=value) if type(value) is not str or valid_message(value):
                continue
            case NAIR():
                continue
            case ReferenceIR(contract=contract) if contract.slot in FRAME_SLOTS:
                continue
            case BinaryIR(left=left, right=right):
                children = (left, right)
            case ConditionalIR(condition=condition, when_true=yes, when_false=no):
                children = (condition, yes, no)
            case ConvertIR(operand=operand) | UnaryIR(operand=operand):
                children = (operand,)
            case CallIR(operation=operation, arguments=args) if operation in CONVERSIONS:
                children = tuple(argument.expression for argument in args)
            case _:
                raise ValueError('Order fill messages require literals, primary bar fields, scalar operators or tostring.')
        pending.extend((child, depth + 1) for child in children)
