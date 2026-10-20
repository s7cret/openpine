"""Admit typed IR once, then evaluate frames supplied by execution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from packages.contracts import (
    BarFrame, CallIR, ConvertIR, Diagnostic, HISTORICAL_PROFILE_ID, IndicatorDeclaration,
    LITERAL_PROFILE_ID, LiteralIR, OpenPineError, PlotOutput, ProgramIR, ReferenceIR,
    RunResult, UnaryIR, ValueType, BinaryIR, ConditionalIR, IfIR, LoadIR, NAIR, StoreIR,
)
from packages.runtime.admission import admit_program
from packages.runtime.operations import KERNELS, PlotEvent
from packages.runtime.series import KernelError
from packages.runtime.state import KernelContext


def program_hash(program: ProgramIR) -> str:
    raw = json.dumps(asdict(program), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fail(code: str, message: str, node=None, category="ARTIFACT_INCOMPATIBLE"):
    raise OpenPineError(Diagnostic(category, code, message, getattr(node, "span", None)))


def value_matches(value, value_type: ValueType) -> bool:
    if value_type.qualifier not in ("const", "input", "simple", "series"):
        return False
    if value is None:
        return value_type.base in ("int", "float")
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
        admit_program(self, program, fail, value_matches)

    def run(self, program: ProgramIR) -> RunResult:
        if program.identity.implementation_profile_id != LITERAL_PROFILE_ID:
            fail("BARS_REQUIRED", "Historical execution requires confirmed bar frames.", category="INPUT_UNAVAILABLE")
        evaluation = self.prepare(program)
        evaluation.step()
        return evaluation.finish()

    def prepare(self, program: ProgramIR):
        # Validate the entire program before evaluating any effectful operation.
        self.admit(program)
        return Evaluation(program)


class Evaluation:
    """Private state of a single run; no state survives a new prepare()."""

    def __init__(self, program):
        self.program = program
        self.states = {}
        self.outputs = {}
        self.frame = None
        self.times = []
        self.context_id = None
        self.count = 0
        self.closed = False
        self.persistent = {}
        self.values = {}
        self.declaration = self.evaluate(program.statements[0])

    def execute(self, node):
        if isinstance(node, StoreIR):
            key = node.binding.binding_id
            if node.declaration and node.binding.mode == "var" and key in self.persistent:
                self.values[key] = self.persistent[key]
                return
            value = self.evaluate(node.expression)
            if not value_matches(value, node.binding.value_type):
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
            return (left and right) if operator == "and" else (left or right)
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
        if node.value_type.base == "int":
            return (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
        return left / right

    def evaluate(self, node):
        if isinstance(node, LiteralIR):
            return node.value
        if isinstance(node, NAIR):
            return None
        if isinstance(node, LoadIR):
            if node.binding_id not in self.values:
                fail("VARIABLE_UNINITIALIZED", "Variable was not initialized on this execution path.", node)
            return self.values[node.binding_id]
        if isinstance(node, ReferenceIR):
            if self.frame is None:
                fail("BARS_REQUIRED", "A context reference requires a current bar.", node, "INPUT_UNAVAILABLE")
            return self.frame.close
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
            context = KernelContext(node.call_site_id, self.frame.context_id if self.frame else "literal", self.states)
            try:
                value = KERNELS[node.operation].function(tuple(arguments), context)
            except KernelError as error:
                raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), node.span,
                                               error.rule_id, self.program.identity.language_version)) from error
            if isinstance(value, IndicatorDeclaration):
                return value
            if isinstance(value, PlotEvent):
                if node.call_site_id not in self.outputs:
                    self.outputs[node.call_site_id] = (value.title, node.arguments[0].expression.value_type, [])
                self.outputs[node.call_site_id][2].append(value.value)
                return None
        if not value_matches(value, node.value_type):
            raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "VALUE_RANGE", "Result exceeds the numeric or string range.", node.span,
                                           language_version=self.program.identity.language_version))
        return value

    def step(self, frame: BarFrame | None = None):
        if self.closed:
            fail("EXECUTION_CLOSED", "This evaluation has already finished.")
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            if type(frame) is not BarFrame or frame.bar_index != self.count or type(frame.bar_index) is not int or type(frame.time) is not int or not 0 <= frame.time < 2**63 or (self.times and frame.time <= self.times[-1]):
                fail("BAR_ORDER", "Historical frames must be supplied once in chronological order.")
            if not value_matches(frame.close, ValueType("float", "series")) or type(frame.context_id) is not str or not frame.context_id or (self.context_id is not None and frame.context_id != self.context_id):
                fail("BAR_CONTEXT", "A run requires finite close values and one fixed context.")
            self.context_id = frame.context_id
            self.times.append(frame.time)
        elif frame is not None or self.count:
            fail("BAR_PROFILE", "The literal profile accepts one synthetic observation.")
        self.frame = frame
        self.values = dict(self.persistent)
        try:
            for statement in self.program.statements[1:]:
                self.execute(statement)
        except OpenPineError:
            self.closed = True  # An incomplete bar cannot be resumed or published as a result.
            raise
        self.count += 1

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
