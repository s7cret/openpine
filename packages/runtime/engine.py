"""Admit typed IR once, then evaluate frames supplied by execution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from packages.contracts import (
    BarFrame, CallIR, ConvertIR, Diagnostic, HISTORICAL_PROFILE_ID, IndicatorDeclaration,
    LITERAL_PROFILE_ID, LiteralIR, OpenPineError, PlotOutput, ProgramIR, ReferenceIR,
    RunResult, UnaryIR, ValueType,
)
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
        return value_type.qualifier == "series" and value_type.base in ("int", "float")
    expected = {"int": int, "float": float, "bool": bool, "string": str}.get(value_type.base)
    return type(value) is expected and (type(value) is not float or math.isfinite(value)) and (
        type(value) is not int or -(2**63) <= value <= 2**63 - 1
    )


class Runtime:
    def __init__(self, identity, contracts, references=()):
        self.identity = identity
        self.contracts = {contract.overload_id: contract for contract in contracts}
        self.references = {contract.symbol_id: contract for contract in references}

    def admit(self, program: ProgramIR) -> None:
        if not isinstance(program, ProgramIR) or program.schema_version != "openpine.typed-ir.2":
            fail("IR_SCHEMA", "Unsupported IR schema.")
        if program.identity != self.identity:
            fail("IR_IDENTITY", "Program source/specification/implementation identity differs from this session.")
        if self.identity.implementation_profile_id not in (LITERAL_PROFILE_ID, HISTORICAL_PROFILE_ID):
            fail("IR_PROFILE", "Unknown implementation profile.")
        sites = set()
        count = 0

        def visit(node, depth=0, top_level=False):
            nonlocal count
            count += 1
            if count > 4096 or depth > 64:
                fail("IR_LIMIT", "IR size/depth limit exceeded.", node)
            if isinstance(node, LiteralIR):
                if node.value_type.qualifier != "const" or not value_matches(node.value, node.value_type):
                    fail("IR_LITERAL_TYPE", "Literal does not match its declared type/range.", node)
            elif isinstance(node, ReferenceIR):
                contract = self.references.get(node.contract.symbol_id)
                if contract != node.contract or node.value_type != contract.value_type or contract.slot != "close" or contract.value_type != ValueType("float", "series"):
                    fail("IR_REFERENCE", "Reference differs from the admitted context slot.", node)
                if self.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                    fail("IR_PROFILE", "Context references require the historical profile.", node)
            elif isinstance(node, ConvertIR):
                visit(node.operand, depth + 1)
                if node.operand.value_type.base != "int" or node.value_type != ValueType("float", node.operand.value_type.qualifier):
                    fail("IR_CONVERSION", "Only qualifier-preserving int-to-float conversion is supported.", node)
            elif isinstance(node, UnaryIR):
                visit(node.operand, depth + 1)
                if node.operator not in ("+", "-") or node.operand.value_type.base not in ("int", "float") or node.value_type != node.operand.value_type:
                    fail("IR_UNARY", "Invalid numeric unary operation.", node)
            elif isinstance(node, CallIR):
                contract = self.contracts.get(node.overload_id)
                if not contract or any(getattr(node, name) != getattr(contract, name) for name in (
                    "symbol_id", "assertion_id", "signature_sha256", "operation", "effects"
                )):
                    fail("IR_CALL_CONTRACT", "Call differs from its admitted operation contract.", node)
                kernel = KERNELS.get(node.operation)
                if not kernel or node.effects != kernel.effects:
                    fail("IR_CAPABILITY", "Runtime operation/effects are unavailable.", node)
                if "updates_state" in node.effects and self.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                    fail("IR_PROFILE", "Stateful operations require the historical profile.", node)
                if node.call_site_id != f"call:{node.span.start}:{node.span.end}" or node.call_site_id in sites:
                    fail("IR_CALL_SITE", "Invalid or repeated call-site identity.", node)
                sites.add(node.call_site_id)
                if set(node.effects) & {"declaration", "emits_plot"} and not top_level:
                    fail("IR_EFFECT_POSITION", "Declarations and output calls must remain global.", node)
                if tuple(argument.parameter_id for argument in node.arguments) != contract.parameter_ids:
                    fail("IR_PARAMETER_IDS", "Bound parameter sequence differs from the admitted signature.", node)
                for argument in node.arguments:
                    if argument.origin not in ("source", "default") or (argument.origin == "default" and argument.source_index is not None):
                        fail("IR_ARGUMENT_ORIGIN", "Invalid bound argument origin.", node)
                source_indices = [argument.source_index for argument in node.arguments if argument.origin == "source"]
                if any(type(item) is not int for item in source_indices) or sorted(source_indices) != list(range(len(source_indices))):
                    fail("IR_EVALUATION_ORDER", "Invalid source argument positions.", node)
                expected_order = tuple(sorted(range(len(node.arguments)), key=lambda position: (
                    node.arguments[position].origin == "default",
                    node.arguments[position].source_index if node.arguments[position].source_index is not None else position,
                )))
                if node.evaluation_order != expected_order:
                    fail("IR_EVALUATION_ORDER", "Argument evaluation order has changed.", node)
                for position, argument in enumerate(node.arguments):
                    visit(argument.expression, depth + 1)
                    value_type = argument.expression.value_type
                    qualifiers = ("const", "input", "simple", "series")
                    if value_type.base not in contract.parameter_types[position] or value_type.qualifier not in qualifiers or qualifiers.index(value_type.qualifier) > qualifiers.index(contract.qualifier_limits[position]):
                        fail("IR_ARGUMENT_TYPE", "Bound argument has an incompatible type.", node)
                expected_type = node.arguments[contract.result_parameter].expression.value_type if contract.result_parameter is not None else contract.result_type
                if node.value_type != expected_type:
                    fail("IR_RESULT_TYPE", "Call result type differs from its contract.", node)
            else:
                fail("IR_NODE", "Unknown IR node.", node)

        for statement in program.statements:
            if not isinstance(statement, CallIR):
                fail("IR_STATEMENT", "Only call statements are implemented.")
            visit(statement, top_level=True)
        if not program.statements or program.statements[0].operation != "harness.indicator" or sum(
            statement.operation == "harness.indicator" for statement in program.statements
        ) != 1 or not any(statement.operation == "visual.plot" for statement in program.statements):
            fail("IR_PROGRAM_SHAPE", "One declaration followed by at least one output is required.")

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
        self.declaration = self.evaluate(program.statements[0])

    def evaluate(self, node):
        if isinstance(node, LiteralIR):
            return node.value
        if isinstance(node, ReferenceIR):
            if self.frame is None:
                fail("BARS_REQUIRED", "A context reference requires a current bar.", node, "INPUT_UNAVAILABLE")
            return self.frame.close
        if isinstance(node, (UnaryIR, ConvertIR)):
            operand = self.evaluate(node.operand)
            if operand is None:
                value = None
            elif isinstance(node, ConvertIR):
                value = float(operand)
            else:
                value = -operand if node.operator == "-" else operand
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
            fail("NUMERIC_RANGE", "Numeric result is outside the supported finite range.", node, "PROFILE_UNCOVERED")
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
        try:
            for statement in self.program.statements[1:]:
                self.evaluate(statement)
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
