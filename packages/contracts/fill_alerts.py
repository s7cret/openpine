"""Version and expression boundaries for local strategy fill alerts."""
from .alerts import valid_message
from .context_ir import ir_nodes
from .model import BinaryIR, CallIR, ConditionalIR, ConvertIR, LiteralIR, NAIR, ReferenceIR, UnaryIR
from .trading import STRATEGY_DECLARATIONS


PROFILE = 'openpine.strategy-fill-alerts.1'
EXTENDED_PROFILE = 'openpine.strategy-fill-alerts.2'
MESSAGE = 'alert_message'
DISABLE = 'disable_alert'
EXIT_MESSAGES = {'limit': 'alert_profit', 'stop': 'alert_loss', 'trailing_stop': 'alert_trailing'}
OPTIONAL_MESSAGES = frozenset(EXIT_MESSAGES.values())
DEFAULTS = {MESSAGE: '', DISABLE: False, **dict.fromkeys(EXIT_MESSAGES.values())}
BASE_FIELDS = (MESSAGE, DISABLE)
EXIT_FIELDS = (*BASE_FIELDS, *EXIT_MESSAGES.values())
SUFFIXES = {(MESSAGE,): '+fill-message', BASE_FIELDS: '+fill-alerts', EXIT_FIELDS: '+exit-fill-alerts'}
EXIT_OPERATIONS = ('simulation.exit', 'simulation.exit_partial', 'simulation.exit_group', 'simulation.exit_extended')
EXTENDED_OPERATIONS = ('simulation.order', 'simulation.close_all', *EXIT_OPERATIONS)
OPERATIONS = ('simulation.entry', 'simulation.entry_pending', 'simulation.entry_group',
              'simulation.close', 'simulation.close_partial', *EXTENDED_OPERATIONS)
FRAME_SLOTS = frozenset(('open', 'high', 'low', 'close', 'volume', 'bar_index', 'time'))
CONVERSIONS = ('scalar.tostring', 'scalar.tostring.format')


def available(version, operation):
    if version < 4:
        return ()
    if version == 4:
        return (MESSAGE,)
    return EXIT_FIELDS if operation in EXIT_OPERATIONS else BASE_FIELDS


def fields(node):
    return next((names for names, suffix in SUFFIXES.items() if node.overload_id.endswith(suffix)), ())


def argument(node, name):
    names = fields(node)
    if name in names:
        return node.arguments[names.index(name) - len(names)].expression


def configuration(program, requested):
    if type(requested) is not bool:
        raise ValueError('order_fill_alerts requires a boolean.')
    if requested and (not program.statements or program.statements[0].operation not in STRATEGY_DECLARATIONS):
        raise ValueError('Order fill alerts require a strategy.')
    return requested or any(isinstance(node, CallIR) and node.operation in OPERATIONS and fields(node)
                            for node in ir_nodes(program.statements))


def validate(node, version):
    names = fields(node)
    if not names:
        return
    legacy_close_all = node.operation == 'simulation.close_all' and version == 5 and names == (MESSAGE,)
    if node.operation not in OPERATIONS or names != available(version, node.operation) and not legacy_close_all:
        raise ValueError('Order fill alert arguments differ from their version profile.')
    for name, argument in zip(names, node.arguments[-len(names):], strict=True):
        expr = argument.expression
        if argument.origin == 'default':
            expected = DEFAULTS[name]
            matches = (isinstance(expr, NAIR) if expected is None else isinstance(expr, LiteralIR)
                       and type(expr.value) is type(expected) and expr.value == expected)
            if not matches:
                raise ValueError('Order fill alert default differs from its contract.')
        if name != DISABLE:
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
