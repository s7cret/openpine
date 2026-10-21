"""Admit typed IR once, then evaluate frames supplied by execution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from packages.contracts import (
    BarFrame, CallIR, ConvertIR, Diagnostic, HISTORICAL_PROFILE_ID, IndicatorDeclaration,
    LITERAL_PROFILE_ID, LiteralIR, OpenPineError, PlotOutput, ProgramIR, ReferenceIR,
    RunResult, UnaryIR, ValueType, BinaryIR, ConditionalIR, IfIR, LoadIR, NAIR, StoreIR, HistoryIR,
    BlockIR, EvaluateIR, UserCallIR,
)
from packages.runtime.admission import admit_program
from packages.runtime.operations import KERNELS, PlotEvent
from packages.runtime.series import KernelError
from packages.runtime.state import KernelContext
from packages.runtime.history import HistoryStore, StorageBudget


def program_hash(program: ProgramIR) -> str:
    raw = json.dumps(asdict(program), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fail(code: str, message: str, node=None, category="ARTIFACT_INCOMPATIBLE"):
    raise OpenPineError(Diagnostic(category, code, message, getattr(node, "span", None)))


def value_matches(value, value_type: ValueType, version=6) -> bool:
    if value_type.qualifier not in ("const", "input", "simple", "series"):
        return False
    if value is None:
        return value_type.base in ("int", "float") or (value_type.base == "bool" and version < 6)
    expected = {"int": int, "float": float, "bool": bool, "string": str}.get(value_type.base)
    return type(value) is expected and (type(value) is not float or math.isfinite(value)) and (
        type(value) is not int or -(2**63) <= value <= 2**63 - 1
    ) and (
        type(value) is not str or len(value) <= 65536
    )


class Runtime:
    def __init__(self, identity, contracts, references=()):
        self.identity = identity
        self.contracts = {contract.overload_id: contract for contract in contracts}
        self.references = {contract.symbol_id: contract for contract in references}

    def admit(self, program: ProgramIR) -> None:
        admit_program(self, program, fail, lambda v, t: value_matches(v, t, self.identity.language_version))

    def run(self, program: ProgramIR) -> RunResult:
        if program.identity.implementation_profile_id != LITERAL_PROFILE_ID:
            fail("BARS_REQUIRED", "Historical execution requires confirmed bar frames.", category="INPUT_UNAVAILABLE")
        evaluation = self.prepare(program)
        evaluation.step()
        return evaluation.finish()

    def prepare(self, program: ProgramIR, *, output_sink=None):
        # Validate the entire program before evaluating any effectful operation.
        self.admit(program)
        return Evaluation(program, output_sink=output_sink)


class Evaluation:
    """Private state of a single run; no state survives a new prepare()."""

    def __init__(self, program, *, output_sink=None):
        self.program = program
        self.states = {}
        self.outputs = {}
        self.frame = None
        self.times = []
        self.output_sink = output_sink
        self.first_time = None
        self.last_time = None
        self.context_id = None
        self.count = 0
        self.closed = False
        self.at_boundary = True
        self.pending_outputs = []
        self.request_values = {}
        self.strategy_values = {}
        self.order_intents = []
        self.persistent = {}
        self.values = {}
        self.budget = StorageBudget()
        self.history = HistoryStore(program, self.budget)
        self.declaration = self.evaluate(program.statements[0])

    def matches(self, value, value_type):
        return value_matches(value, value_type, self.program.identity.language_version)

    def execute(self, node):
        if isinstance(node, EvaluateIR):
            self.evaluate(node.expression)
        elif isinstance(node, StoreIR):
            key = node.binding.binding_id
            if node.declaration and node.binding.mode == "var" and key in self.persistent:
                self.values[key] = self.persistent[key]
                return
            value = self.evaluate(node.expression)
            if not self.matches(value, node.binding.value_type):
                fail("STORE_VALUE", "Stored value does not match its variable type.", node)
            self.values[key] = value
            if node.binding.mode == "var":
                self.persistent[key] = value
        elif isinstance(node, IfIR):
            chosen = node.body if self.evaluate(node.condition) else node.otherwise
            for statement in chosen:
                self.execute(statement)
        else:
            self.evaluate(node)

    def binary(self, node):
        left = self.evaluate(node.left)
        operator = node.operator
        if self.program.language_policy.logical_evaluation == "lazy":
            if operator == "and" and left is False:
                return False
            if operator == "or" and left is True:
                return True
        right = self.evaluate(node.right)
        if operator in ("and", "or"):
            if operator == "and":
                return False if left is False or right is False else None if left is None or right is None else True
            return True if left is True or right is True else None if left is None or right is None else False
        if operator in ("==", "!=", "<", "<=", ">", ">="):
            if left is None or right is None:
                return self.program.language_policy.comparison_na
            if operator == "==":
                return left == right
            if operator == "!=":
                return left != right
            if operator == "<":
                return left < right
            if operator == "<=":
                return left <= right
            if operator == ">":
                return left > right
            return left >= right
        if left is None or right is None:
            return None
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if right == 0:
            return None
        if operator == "%":
            return left % right
        if node.value_type.base == "int":
            return (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
        return left / right

    def evaluate(self, node):
        if isinstance(node,CallIR) and node.operation.startswith('simulation.'):
            from packages.contracts.trading import OrderIntent,StrategyDeclaration,decimal_text
            arguments=[None]*len(node.arguments)
            for position in node.evaluation_order:arguments[position]=self.evaluate(node.arguments[position].expression)
            if node.operation=='simulation.strategy':return StrategyDeclaration(arguments[0],arguments[1])
            if not self.strategy_values:fail('BROKER_REQUIRED','Strategy broker was not scheduled.',node,'INPUT_UNAVAILABLE')
            if self.program.identity.language_version<6 and not arguments[-1]:return None
            if len(self.order_intents)>=16:fail('ORDER_LIMIT','At most 16 reached commands per bar are admitted.',node,'PROFILE_UNCOVERED')
            entry=node.operation=='simulation.entry'
            try:
                direction=((1 if arguments[1] else -1) if self.program.identity.language_version<5 else (1 if arguments[1]=='long' else -1)) if entry else 0
                quantity=decimal_text(arguments[2],positive=True) if entry and arguments[2] is not None else None
                self.order_intents.append(OrderIntent(node.call_site_id,'entry' if entry else 'close',arguments[0],direction if entry else 0,quantity))
            except ValueError as error:fail('ORDER_QUANTITY',str(error),node,'RUNTIME_ERROR')
            return None
        if isinstance(node,CallIR) and node.operation=="context.request":
            if node.call_site_id not in self.request_values:
                fail("CONTEXT_INPUT_REQUIRED","Request context was not scheduled.",node,"INPUT_UNAVAILABLE")
            value=self.request_values[node.call_site_id]
            if not self.matches(value,node.value_type):fail("REQUEST_VALUE","Invalid requested value.",node)
            return value
        if isinstance(node, (BlockIR, UserCallIR)):
            if isinstance(node, UserCallIR):
                arguments = [None] * len(node.arguments)
                for index in node.evaluation_order:
                    arguments[index] = self.evaluate(node.arguments[index].expression)
                for parameter, value in zip(node.parameters, arguments, strict=True):
                    if not self.matches(value, parameter.value_type):
                        fail("FUNCTION_PARAMETER_VALUE", "Function argument has an incompatible value.", node)
                    self.values[parameter.binding_id] = value
            for statement in node.statements:
                self.execute(statement)
            return self.evaluate(node.result)
        if isinstance(node, LiteralIR):
            return node.value
        if isinstance(node, NAIR):
            return None
        if isinstance(node, LoadIR):
            if node.binding_id not in self.values:
                fail("VARIABLE_UNINITIALIZED", "Variable was not initialized on this execution path.", node)
            return self.values[node.binding_id]
        if isinstance(node, ReferenceIR):
            if node.contract.slot.startswith('strategy.'):
                key=node.contract.slot.removeprefix('strategy.')
                if key not in self.strategy_values:fail('BROKER_REQUIRED','Strategy broker was not scheduled.',node,'INPUT_UNAVAILABLE')
                return self.strategy_values[key]
            if self.frame is None:
                fail("BARS_REQUIRED", "A context reference requires a current bar.", node, "INPUT_UNAVAILABLE")
            return getattr(self.frame, node.contract.slot)
        if isinstance(node, HistoryIR):
            current, offset = self.evaluate(node.operand), self.evaluate(node.offset)
            try:
                return self.history.read(node, offset, current, self.program.identity.language_version)
            except KernelError as error:
                raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), node.span,
                    error.rule_id, self.program.identity.language_version)) from error
        if isinstance(node, BinaryIR):
            value = self.binary(node)
        elif isinstance(node, ConditionalIR):
            chosen = node.when_true if self.evaluate(node.condition) else node.when_false
            value = self.evaluate(chosen)
        elif isinstance(node, (UnaryIR, ConvertIR)):
            operand = self.evaluate(node.operand)
            if isinstance(node, ConvertIR) and node.value_type.base == "bool":
                value = False if operand is None else bool(operand)
            elif operand is None:
                value = None
            elif isinstance(node, ConvertIR):
                value = float(operand)
            else:
                value = not operand if node.operator == "not" else -operand if node.operator == "-" else operand
        else:
            arguments = [None] * len(node.arguments)
            for position in node.evaluation_order:
                arguments[position] = self.evaluate(node.arguments[position].expression)
            context = KernelContext(node.call_site_id, self.frame.context_id if self.frame else "literal", self.states,
                                    self.budget, self.program.identity.language_version)
            try:
                value = KERNELS[node.operation].function(tuple(arguments), context)
            except KernelError as error:
                raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), node.span,
                                               error.rule_id, self.program.identity.language_version)) from error
            if isinstance(value, IndicatorDeclaration):
                return value
            if isinstance(value, PlotEvent):
                self.pending_outputs.append((node.call_site_id,value.title,
                    node.arguments[0].expression.value_type,value.value))
                return None
        if not self.matches(value, node.value_type):
            raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "VALUE_RANGE", "Result exceeds the numeric or string range.", node.span,
                                           language_version=self.program.identity.language_version))
        return value

    def step(self, frame: BarFrame | None = None):
        self._validate_frame(frame)
        self.at_boundary = False
        try:
            self._step(frame)
            self.at_boundary = True
        except BaseException:
            self.closed = True
            self.pending_outputs.clear()
            raise

    def _validate_frame(self, frame):
        if self.closed:
            fail("EXECUTION_CLOSED", "This evaluation has already finished.")
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            if type(frame) is not BarFrame or frame.bar_index != self.count or type(frame.bar_index) is not int or type(frame.time) is not int or not 0 <= frame.time < 2**63 or (self.last_time is not None and frame.time <= self.last_time):
                fail("BAR_ORDER", "Historical frames must be supplied once in chronological order.")
            if not value_matches(frame.close, ValueType("float", "series")) or type(frame.context_id) is not str or not frame.context_id or (self.context_id is not None and frame.context_id != self.context_id):
                fail("BAR_CONTEXT", "A run requires finite close values and one fixed context.")
            if any(not value_matches(getattr(frame, slot), ValueType("float", "series")) for slot in ("open", "high", "low", "volume")) or (frame.volume is not None and frame.volume < 0):
                fail("BAR_CONTEXT", "OHLCV values must be finite numbers or na; volume cannot be negative.")
            prices = [v for v in (frame.open, frame.close) if v is not None]
            if (frame.high is not None and any(frame.high < v for v in [*prices, *([frame.low] if frame.low is not None else [])])) or (frame.low is not None and any(frame.low > v for v in prices)):
                fail("BAR_CONTEXT", "OHLC values are inconsistent.")
        elif frame is not None or self.count:
            fail("BAR_PROFILE", "The literal profile accepts one synthetic observation.")

    def _step(self, frame):
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            self.context_id = frame.context_id
            if self.first_time is None:
                self.first_time = frame.time
            self.last_time = frame.time
            if self.output_sink is None:
                self.times.append(frame.time)
        self.frame = frame
        self.pending_outputs.clear()
        self.values = dict(self.persistent)
        try:
            for statement in self.program.statements[1:]:
                self.execute(statement)
            self.history.commit(frame, self.values,self.strategy_values)
            # Output becomes eligible only after every statement and history
            # update succeeded. A sink failure poisons this evaluation; its
            # incomplete tail can never become a checkpoint or final result.
            for site,title,value_type,value in self.pending_outputs:
                if site not in self.outputs:
                    self.outputs[site] = (title,value_type,[])
                if self.output_sink is None:
                    self.outputs[site][2].append(value)
                else:
                    self.output_sink.emit(site,title,value_type,value,self.count,frame.time)
            self.pending_outputs.clear()
        except KernelError as error:
            self.closed = True
            raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), rule_id=error.rule_id,
                language_version=self.program.identity.language_version)) from error
        except OpenPineError:
            self.closed = True  # An incomplete bar cannot be resumed or published as a result.
            raise
        self.count += 1

    def checkpoint(self):
        from packages.runtime.checkpoints import export_state
        return export_state(self)

    def restore(self, state):
        from packages.runtime.checkpoints import restore_state
        restore_state(self,state)

    def finish(self, data=None) -> RunResult:
        if self.closed or not self.count:
            fail("EXECUTION_COUNT", "A completed, nonempty evaluation is required.")
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            if data is None or data.context_id != self.context_id or data.times != tuple(self.times):
                fail("DATA_IDENTITY", "Result data identity does not match the executed frames.")
        elif data is not None:
            fail("DATA_IDENTITY", "Literal results have no bar dataset.")
        self.closed = True
        outputs = tuple(PlotOutput(site, title, value_type, tuple(values))
                        for site, (title, value_type, values) in self.outputs.items())
        return RunResult(self.program.identity, program_hash(self.program), self.declaration, outputs, self.count, data)

    def finish_stream(self, data):
        """Finalize a sink-backed run without retaining timestamps or plot arrays."""
        if (self.closed or self.output_sink is None or not self.count or
                (data.context_id, data.count, data.first_ms, data.last_ms) !=
                (self.context_id, self.count, self.first_time, self.last_time)):
            fail("DATA_IDENTITY", "Stream identity does not match the executed frames.")
        self.closed = True
        return {"schema_version": "openpine.run-summary.3", "observation_count": self.count,
            "declaration": asdict(self.declaration), "identity": asdict(self.program.identity),
            "program_sha256": program_hash(self.program), "data": asdict(data)}
