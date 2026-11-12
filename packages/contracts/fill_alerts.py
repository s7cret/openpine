"""Version and expression boundaries for local strategy fill alerts."""
from dataclasses import replace

from .alerts import valid_message
from .context_ir import ir_nodes
from .model import BinaryIR, CallIR, ConditionalIR, ConvertIR, InputIR, LiteralIR, LoadIR, NAIR, ReferenceIR, StoreIR, UnaryIR
from .trading import STRATEGY_DECLARATIONS


PROFILE = 'openpine.strategy-fill-alerts.1'
EXTENDED_PROFILE = 'openpine.strategy-fill-alerts.2'
BINDINGS_PROFILE = 'openpine.strategy-fill-alerts.3'
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
SCALARS = frozenset(('int', 'float', 'bool', 'string'))


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


def validate(node, version, bindings):
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
            bindings.resolve(expr)


class MessageBindings:
    """Inline pure aliases and keep only persistent scalar loads for replay."""

    def __init__(self, program):
        self.program = program
        self.declarations = None
        self.persistent = {}
        self.used = False

    def declaration(self, key):
        if self.declarations is None:
            self.declarations = {node.binding.binding_id: node for node in self.program.statements
                                 if isinstance(node, StoreIR) and node.declaration}
            self.modified = {node.binding.binding_id for node in ir_nodes(self.program.statements)
                             if isinstance(node, StoreIR) and not node.declaration}
        store = self.declarations.get(key)
        if store is None or store.binding.scope_id != 'scope:root' or store.binding.namespace:
            raise ValueError('Order fill messages require unconditional global scalar bindings.')
        binding = store.binding
        if (binding.value_type.base not in SCALARS or binding.mode not in ('bar', 'var')
                or binding.mode == 'bar' and key in self.modified):
            raise ValueError('Order fill messages require immutable scalar declarations or global scalar var bindings.')
        self.used = True
        return store

    def resolve(self, expression):
        remaining = 256

        def expand(node, depth=1, active=()):
            nonlocal remaining
            remaining -= 1
            if remaining < 0 or depth > 32:
                raise ValueError('Order fill message exceeds its expression budget.')
            deeper = depth + 1
            match node:
                case LiteralIR(value=value) if type(value) is not str or valid_message(value):
                    return node
                case NAIR():
                    return node
                case ReferenceIR(contract=contract) if contract.slot in FRAME_SLOTS:
                    return node
                case InputIR(definition=definition) if definition.input_type in SCALARS:
                    return node
                case LoadIR(binding_id=key):
                    store = self.declaration(key)
                    if store.binding.mode == 'var':
                        self.persistent[key] = (False if store.binding.value_type.base == 'bool'
                                               and self.program.identity.language_version == 6 else None)
                        return node
                    if key in active:
                        raise ValueError('Order fill message dependencies contain a cycle.')
                    return expand(store.expression, deeper, (*active, key))
                case BinaryIR(left=left, right=right):
                    return replace(node, left=expand(left, deeper, active), right=expand(right, deeper, active))
                case ConditionalIR(condition=condition, when_true=yes, when_false=no):
                    return replace(node, condition=expand(condition, deeper, active),
                                   when_true=expand(yes, deeper, active), when_false=expand(no, deeper, active))
                case ConvertIR(operand=operand) | UnaryIR(operand=operand):
                    return replace(node, operand=expand(operand, deeper, active))
                case CallIR(operation=operation, arguments=args) if operation in CONVERSIONS:
                    return replace(node, arguments=tuple(replace(arg, expression=expand(arg.expression, deeper, active))
                                                         for arg in args))
                case _:
                    raise ValueError('Order fill messages require pure scalar expressions over bar fields, inputs or admitted globals.')

        return expand(expression)
