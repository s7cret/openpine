"""Validate an entire typed program and its lexical scopes before effects."""

import re

from packages.contracts import (
    BinaryIR, CallIR, ConditionalIR, ConvertIR, IfIR, LiteralIR, LoadIR, NAIR,
    ProgramIR, ReferenceIR, StoreIR, UnaryIR, ValueType, VariableContract,
    HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID,
)
from packages.runtime.language import language_policy
from packages.runtime.operations import KERNELS
from packages.runtime.types import QUALIFIERS, SCALARS, binary_type, qualifier, valid_type


def admit_program(runtime, program, fail, value_matches):
    if not isinstance(program, ProgramIR) or program.schema_version != "openpine.typed-ir.3":
        fail("IR_SCHEMA", "Unsupported IR schema.")
    if program.identity != runtime.identity:
        fail("IR_IDENTITY", "Program identity differs from this session.")
    if runtime.identity.implementation_profile_id not in (LITERAL_PROFILE_ID, HISTORICAL_PROFILE_ID):
        fail("IR_PROFILE", "Unknown implementation profile.")
    policy = language_policy(runtime.identity.language_version)
    if program.language_policy != policy:
        fail("IR_LANGUAGE_POLICY", "Program language policy differs from this runtime.")
    bindings = {}
    for binding in program.bindings:
        if not isinstance(binding, VariableContract) or binding.binding_id in bindings or not valid_type(binding.value_type) or binding.value_type.base not in SCALARS or binding.mode not in ("bar", "var") or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", binding.name):
            fail("IR_BINDING", "Invalid or repeated variable contract.")
        if binding.binding_id != f"binding:{binding.span.start}:{binding.span.end}" or (binding.mode == "var" and binding.value_type.qualifier != "series"):
            fail("IR_BINDING", "Invalid variable identity or persistence qualifier.")
        bindings[binding.binding_id] = binding
    sites, declarations = set(), set()
    count = 0

    def limit(node, depth):
        nonlocal count
        count += 1
        if count > 4096 or depth > 96:
            fail("IR_LIMIT", "IR size/depth limit exceeded.", node)

    def owner(binding, scopes):
        return next((scope[binding.name] for scope in reversed(scopes) if binding.name in scope), None)

    def visit(node, scopes, depth=0, top_level=False):
        limit(node, depth)
        if not valid_type(getattr(node, "value_type", None)):
            fail("IR_VALUE_TYPE", "Invalid expression type.", node)
        if isinstance(node, LiteralIR):
            if node.value is None or node.value_type.qualifier != "const" or not value_matches(node.value, node.value_type):
                fail("IR_LITERAL_TYPE", "Literal does not match its declared type/range.", node)
        elif isinstance(node, NAIR):
            if node.value_type.base not in ("int", "float") or node.value_type.qualifier != "const":
                fail("IR_NA_TYPE", "na requires a resolved numeric type.", node)
        elif isinstance(node, LoadIR):
            binding = bindings.get(node.binding_id)
            if binding is None or owner(binding, scopes) != binding or node.value_type != binding.value_type:
                fail("IR_VARIABLE_SCOPE", "Load refers to an unavailable or shadowed variable.", node)
        elif isinstance(node, ReferenceIR):
            contract = runtime.references.get(node.contract.symbol_id)
            if contract != node.contract or node.value_type != contract.value_type or contract.slot != "close" or contract.value_type != ValueType("float", "series"):
                fail("IR_REFERENCE", "Reference differs from its admitted context slot.", node)
            if runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE", "Context references require the historical profile.", node)
        elif isinstance(node, ConvertIR):
            visit(node.operand, scopes, depth + 1)
            source = node.operand.value_type
            allowed = (source.base == "int" and node.value_type.base == "float") or (
                policy.numeric_conditions and source.base in ("int", "float") and node.value_type.base == "bool")
            if not allowed or node.value_type.qualifier != source.qualifier:
                fail("IR_CONVERSION", "Invalid explicit conversion.", node)
        elif isinstance(node, UnaryIR):
            visit(node.operand, scopes, depth + 1)
            valid = node.operator in ("+", "-") and node.operand.value_type.base in ("int", "float")
            valid |= node.operator == "not" and node.operand.value_type.base == "bool"
            if not valid or node.value_type != node.operand.value_type:
                fail("IR_UNARY", "Invalid unary operation.", node)
        elif isinstance(node, BinaryIR):
            visit(node.left, scopes, depth + 1)
            visit(node.right, scopes, depth + 1)
            try:
                expected = binary_type(node.operator, node.left.value_type, node.right.value_type, policy)
            except ValueError:
                fail("IR_BINARY", "Invalid binary operation.", node)
            if node.value_type != expected:
                fail("IR_BINARY_TYPE", "Binary result type differs from its operands.", node)
        elif isinstance(node, ConditionalIR):
            for child in (node.condition, node.when_true, node.when_false):
                visit(child, scopes, depth + 1)
            left, right = node.when_true.value_type, node.when_false.value_type
            if node.condition.value_type.base != "bool" or left.base not in SCALARS or left.base != right.base or node.value_type != ValueType(left.base, qualifier(node.condition.value_type, left, right)):
                fail("IR_CONDITIONAL", "Invalid typed conditional.", node)
        elif isinstance(node, CallIR):
            contract = runtime.contracts.get(node.overload_id)
            if not contract or any(getattr(node, name) != getattr(contract, name) for name in (
                "symbol_id", "assertion_id", "signature_sha256", "operation", "effects"
            )):
                fail("IR_CALL_CONTRACT", "Call differs from its admitted contract.", node)
            kernel = KERNELS.get(node.operation)
            if not kernel or node.effects != kernel.effects:
                fail("IR_CAPABILITY", "Runtime operation/effects are unavailable.", node)
            if "updates_state" in node.effects and runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE", "Stateful operations require historical execution.", node)
            if node.call_site_id != f"call:{node.span.start}:{node.span.end}" or node.call_site_id in sites:
                fail("IR_CALL_SITE", "Invalid or repeated call-site identity.", node)
            sites.add(node.call_site_id)
            if set(node.effects) & {"declaration", "emits_plot"} and not top_level:
                fail("IR_EFFECT_POSITION", "Declarations and plot calls must be global.", node)
            if tuple(arg.parameter_id for arg in node.arguments) != contract.parameter_ids:
                fail("IR_PARAMETER_IDS", "Bound parameter sequence differs from its signature.", node)
            for arg in node.arguments:
                if arg.origin not in ("source", "default") or (arg.origin == "default" and arg.source_index is not None):
                    fail("IR_ARGUMENT_ORIGIN", "Invalid argument origin.", node)
            indices = [arg.source_index for arg in node.arguments if arg.origin == "source"]
            if any(type(item) is not int for item in indices) or sorted(indices) != list(range(len(indices))):
                fail("IR_EVALUATION_ORDER", "Invalid source argument positions.", node)
            order = tuple(sorted(range(len(node.arguments)), key=lambda i: (node.arguments[i].origin == "default",
                node.arguments[i].source_index if node.arguments[i].source_index is not None else i)))
            if node.evaluation_order != order:
                fail("IR_EVALUATION_ORDER", "Argument evaluation order has changed.", node)
            for position, arg in enumerate(node.arguments):
                visit(arg.expression, scopes, depth + 1)
                value_type = arg.expression.value_type
                if value_type.base not in contract.parameter_types[position] or QUALIFIERS.index(value_type.qualifier) > QUALIFIERS.index(contract.qualifier_limits[position]):
                    fail("IR_ARGUMENT_TYPE", "Incompatible bound argument type.", node)
            expected = node.arguments[contract.result_parameter].expression.value_type if contract.result_parameter is not None else contract.result_type
            if node.value_type != expected:
                fail("IR_RESULT_TYPE", "Call result type differs from its contract.", node)
        else:
            fail("IR_NODE", "Unknown expression node.", node)

    def statements(nodes, scopes, scope_id="scope:root", depth=0):
        for node in nodes:
            if isinstance(node, StoreIR):
                limit(node, depth)
                binding = bindings.get(node.binding.binding_id)
                if binding != node.binding or type(node.declaration) is not bool:
                    fail("IR_STORE", "Store differs from its variable contract.", node)
                visit(node.expression, scopes, depth + 1)
                if node.expression.value_type.base != binding.value_type.base or QUALIFIERS.index(node.expression.value_type.qualifier) > QUALIFIERS.index(binding.value_type.qualifier):
                    fail("IR_STORE_TYPE", "Store value is incompatible with its variable.", node)
                if node.declaration:
                    if binding.binding_id in declarations or binding.scope_id != scope_id or binding.name in scopes[-1]:
                        fail("IR_DECLARATION", "Duplicate or misplaced variable declaration.", node)
                    declarations.add(binding.binding_id)
                    scopes[-1][binding.name] = binding
                elif owner(binding, scopes) != binding or binding.value_type.qualifier != "series":
                    fail("IR_VARIABLE_SCOPE", "Assignment does not target the nearest mutable owner.", node)
            elif isinstance(node, IfIR):
                limit(node, depth)
                visit(node.condition, scopes, depth + 1)
                if node.condition.value_type.base != "bool" or not node.body:
                    fail("IR_IF", "Invalid condition or empty if body.", node)
                statements(node.body, [*scopes, {}], f"scope:{node.span.start}:then", depth + 1)
                statements(node.otherwise, [*scopes, {}], f"scope:{node.span.start}:else", depth + 1)
            elif isinstance(node, CallIR):
                visit(node, scopes, depth + 1, scope_id == "scope:root")
            else:
                fail("IR_STATEMENT", "Unknown statement node.", node)

    statements(program.statements, [{}])
    if declarations != bindings.keys():
        fail("IR_BINDINGS", "Variable table and declarations differ.")
    if not program.statements or not isinstance(program.statements[0], CallIR) or program.statements[0].operation != "harness.indicator" or sum(
        isinstance(node, CallIR) and node.operation == "harness.indicator" for node in program.statements
    ) != 1 or not any(isinstance(node, CallIR) and node.operation == "visual.plot" for node in program.statements):
        fail("IR_PROGRAM_SHAPE", "One declaration followed by global output is required.")
