"""Iteration control and deterministic per-bar work limits."""

from packages.contracts import Diagnostic, OpenPineError, ForIR, TupleType
from packages.contracts.loops import ITERATIONS_PER_BAR, OPERATIONS_PER_BAR, CANCELLATION_INTERVAL


class LoopTransfer(Exception):
    def __init__(self, action, loop_id):
        self.action, self.loop_id = action, loop_id


class WorkBudget:
    def __init__(self, version):
        self.version = version
        self.reset()

    def reset(self):
        self.iterations = self.operations = 0

    def error(self, code, message, node, category='RUNTIME_ERROR'):
        raise OpenPineError(Diagnostic(category, code, message, node.span,
            'rule.loop', self.version))

    def charge(self, node, cancelled):
        self.operations += 1
        if self.operations > OPERATIONS_PER_BAR:
            self.error('OPERATION_BUDGET', 'Per-bar operation budget exceeded.', node)
        if cancelled and (self.operations == 1 or self.operations % CANCELLATION_INTERVAL == 0) and cancelled():
            self.error('CANCELLED', 'Execution cancelled inside a loop.', node, 'EXECUTION_CANCELLED')

    def iteration(self, node):
        self.iterations += 1
        if self.iterations > ITERATIONS_PER_BAR:
            self.error('ITERATION_BUDGET', 'Per-bar iteration budget exceeded.', node)


def missing(typ, version):
    if isinstance(typ, TupleType):return tuple(missing(t, version) for t in typ.elements)
    return False if typ.base == 'bool' and version == 6 else None


def evaluate_loop(evaluation, node):
    version = evaluation.program.identity.language_version
    result = missing(node.value_type, version)
    if isinstance(node, ForIR):
        current = evaluation.evaluate(node.initial)
        final = evaluation.evaluate(node.final)
        step = evaluation.evaluate(node.step)
        if type(step) is not int or step <= 0:
            evaluation.work.error('LOOP_STEP', 'Loop step must be a defined positive integer.', node.step)
        if current is None or final is None:return result
        direction = 1 if current <= final else -1
    while True:
        if isinstance(node, ForIR):
            if final is None or (current > final if direction == 1 else current < final):break
            evaluation.values[node.counter.binding_id] = current
        elif not evaluation.evaluate(node.condition):break
        evaluation.work.iteration(node)
        try:
            for statement in node.statements:evaluation.execute(statement)
            if node.result is not None:result = evaluation.evaluate(node.result)
        except LoopTransfer as transfer:
            if transfer.loop_id != node.loop_id:raise
            if transfer.action == 'break':break
        if evaluation.first_only_loop == node.loop_id:break
        if isinstance(node, ForIR):
            current += direction * step
            # A terminal increment beyond int64 never becomes a visible value.
            if not -(2**63) <= current < 2**63:break
            if version == 6:final = evaluation.evaluate(node.final)
    return result
