"""Collect alert intents from one successful evaluation pass."""
from packages.contracts.alerts import CONDITION, FREQUENCIES, MAX_PER_PASS, valid_message
from packages.contracts.trading import STRATEGY_DECLARATIONS
from .series import KernelError


def evaluate(evaluation, node):
    values = [None] * len(node.arguments)
    for position in node.evaluation_order:
        values[position] = evaluation.evaluate(node.arguments[position].expression)
    if node.operation == CONDITION:
        condition, _, message = values
        if not condition or evaluation.program.statements[0].operation in STRATEGY_DECLARATIONS:
            return
        frequency = None
    else:
        message, frequency = values
        if frequency not in FREQUENCIES:
            raise KernelError('ALERT_FREQUENCY', 'Unknown alert frequency.', 'rule.alerts')
    if not valid_message(message):
        raise KernelError('ALERT_MESSAGE', 'Alert message exceeds its UTF-8 byte limit.', 'rule.alerts')
    if len(evaluation.alert_intents) >= MAX_PER_PASS:
        raise KernelError('ALERT_LIMIT', 'Too many alerts in one calculation.', 'rule.alerts')
    evaluation.alert_intents.append((node.call_site_id, message, frequency))
