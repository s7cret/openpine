"""Admission and execution of typed literal IR without re-binding calls."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from packages.contracts import (
    CallIR, Diagnostic, IndicatorDeclaration, LiteralIR, OpenPineError,
    PlotOutput, ProgramIR, RunResult, UnaryIR, ValueType,
)
from packages.runtime.operations import KERNELS, PlotEvent


def program_hash(program: ProgramIR) -> str:
    raw = json.dumps(asdict(program), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fail(code: str, message: str, node=None, category="ARTIFACT_INCOMPATIBLE"):
    raise OpenPineError(Diagnostic(category, code, message, getattr(node, "span", None)))


def value_matches(value, value_type: ValueType) -> bool:
    if value_type.qualifier != "const":
        return False
    expected = {"int": int, "float": float, "bool": bool, "string": str}.get(value_type.base)
    return type(value) is expected and (type(value) is not float or math.isfinite(value)) and (
        type(value) is not int or -(2**63) <= value <= 2**63 - 1
    )


class Runtime:
    def __init__(self, identity, contracts):
        self.identity = identity
        self.contracts = {contract.overload_id: contract for contract in contracts}

    def admit(self, program: ProgramIR) -> None:
        if not isinstance(program, ProgramIR) or program.schema_version != "openpine.literal-ir.1":
            fail("IR_SCHEMA", "Unsupported IR schema.")
        if program.identity != self.identity:
            fail("IR_IDENTITY", "Program source/specification/implementation identity differs from this session.")
        sites = set()
        count = 0

        def visit(node, depth=0, top_level=False):
            nonlocal count
            count += 1
            if count > 4096 or depth > 64:
                fail("IR_LIMIT", "IR size/depth limit exceeded.", node)
            if isinstance(node, LiteralIR):
                if not value_matches(node.value, node.value_type):
                    fail("IR_LITERAL_TYPE", "Literal does not match its declared type/range.", node)
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
                if node.call_site_id != f"call:{node.span.start}:{node.span.end}" or node.call_site_id in sites:
                    fail("IR_CALL_SITE", "Invalid or repeated call-site identity.", node)
                sites.add(node.call_site_id)
                if node.effects and not top_level:
                    fail("IR_EFFECT_POSITION", "Effectful calls cannot be nested in this slice.", node)
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
                    if value_type.base not in contract.parameter_types[position] or value_type.qualifier != "const":
                        fail("IR_ARGUMENT_TYPE", "Bound argument has an incompatible type.", node)
                expected_type = node.arguments[contract.result_parameter].expression.value_type if contract.result_parameter is not None else ValueType("void")
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
        # Validate the entire program before evaluating any effectful operation.
        self.admit(program)
        outputs = []
        declaration = None

        def evaluate(node):
            nonlocal declaration
            if isinstance(node, LiteralIR):
                return node.value
            if isinstance(node, UnaryIR):
                operand = evaluate(node.operand)
                value = -operand if node.operator == "-" else operand
            else:
                arguments = [None] * len(node.arguments)
                for position in node.evaluation_order:
                    arguments[position] = evaluate(node.arguments[position].expression)
                value = KERNELS[node.operation].function(tuple(arguments))
                if isinstance(value, IndicatorDeclaration):
                    declaration = value
                    return None
                if isinstance(value, PlotEvent):
                    outputs.append(PlotOutput(node.call_site_id, value.title,
                                              node.arguments[0].expression.value_type, (value.value,)))
                    return None
            if not value_matches(value, node.value_type):
                fail("NUMERIC_RANGE", "Numeric result is outside the supported finite range.", node, "PROFILE_UNCOVERED")
            return value

        for statement in program.statements:
            evaluate(statement)
        return RunResult(program.identity, program_hash(program), declaration, tuple(outputs))
