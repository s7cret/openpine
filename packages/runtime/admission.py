"""Validate an entire typed program and its lexical scopes before effects."""

import re

from packages.contracts import (
    BinaryIR, CallIR, ConditionalIR, ConvertIR, IfIR, LiteralIR, LoadIR, NAIR,
    ProgramIR, ReferenceIR, StoreIR, UnaryIR, ValueType, VariableContract, HistoryIR,
    HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID, BlockIR, EvaluateIR, FunctionContract, ParameterContract, UserCallIR,
)
from packages.contracts.scopes import branch_scope, dependency_qualifier, site_id
from packages.runtime.language import language_policy
from packages.runtime.operations import KERNELS
from packages.runtime.types import QUALIFIERS, SCALARS, binary_type, call_type, qualifier, valid_type


def admit_program(runtime, program, fail, value_matches):
    if not isinstance(program, ProgramIR) or program.schema_version != "openpine.typed-ir.5":
        fail("IR_SCHEMA", "Unsupported IR schema.")
    if program.identity != runtime.identity:
        fail("IR_IDENTITY", "Program identity differs from this session.")
    if runtime.identity.implementation_profile_id not in (LITERAL_PROFILE_ID, HISTORICAL_PROFILE_ID):
        fail("IR_PROFILE", "Unknown implementation profile.")
    policy = language_policy(runtime.identity.language_version)
    from packages.contracts.context_ir import request_nodes
    request_nodes(program)
    if program.language_policy != policy:
        fail("IR_LANGUAGE_POLICY", "Program language policy differs from this runtime.")
    bindings = {}
    for binding in program.bindings:
        if not isinstance(binding, VariableContract) or binding.binding_id in bindings or not valid_type(binding.value_type) or binding.value_type.base not in SCALARS or binding.mode not in ("bar", "var", "parameter") or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", binding.name):
            fail("IR_BINDING", "Invalid or repeated variable contract.")
        if binding.binding_id != site_id("binding", binding.span, binding.namespace) or (binding.mode == "var" and binding.value_type.qualifier != "series"):
            fail("IR_BINDING", "Invalid variable identity or persistence qualifier.")
        bindings[binding.binding_id] = binding
    functions, names = {}, set()
    if len(program.functions) > 64:
        fail("IR_FUNCTION", "Too many function definitions.")
    for function in program.functions:
        if not isinstance(function, FunctionContract) or function.function_id != site_id("function", function.span) or function.function_id in functions or function.name in names or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", function.name):
            fail("IR_FUNCTION", "Invalid or repeated function identity.")
        if len(function.parameters) > 16 or len({p.name for p in function.parameters}) != len(function.parameters) or len(set(function.captures)) != len(function.captures):
            fail("IR_FUNCTION", "Invalid function parameters/captures.")
        for parameter in function.parameters:
            if not isinstance(parameter, ParameterContract) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", parameter.name) or parameter.annotation not in (*SCALARS, None) or parameter.qualifier not in ("simple", "series", None) or (parameter.qualifier and not parameter.annotation) or not function.span.start <= parameter.span.start < parameter.span.end <= function.span.end:
                fail("IR_FUNCTION", "Invalid function parameter specification.")
        if any(key not in bindings or bindings[key].scope_id != "scope:root" for key in function.captures):
            fail("IR_FUNCTION_CAPTURE", "Function captures must name global bindings.")
        functions[function.function_id] = function
        names.add(function.name)
    sites, declarations = set(), set()
    count = 0
    namespace, function_stack = "", ()
    instances = 0
    root_scope = {}

    def limit(node, depth):
        nonlocal count
        count += 1
        if count > 8192 or depth > 128:
            fail("IR_LIMIT", "IR size/depth limit exceeded.", node)

    def owner(binding, scopes):
        return next((scope[binding.name] for scope in reversed(scopes) if binding.name in scope), None)

    def visit(node, scopes, depth=0, top_level=False):
        nonlocal namespace, function_stack, instances
        limit(node, depth)
        if not valid_type(getattr(node, "value_type", None)):
            fail("IR_VALUE_TYPE", "Invalid expression type.", node)
        if isinstance(node, LiteralIR):
            if node.value is None or node.value_type.qualifier != "const" or not value_matches(node.value, node.value_type):
                fail("IR_LITERAL_TYPE", "Literal does not match its declared type/range.", node)
        elif isinstance(node, NAIR):
            if node.value_type.base not in (("int", "float", "bool") if policy.language_version < 6 else ("int", "float")) or node.value_type.qualifier != "const":
                fail("IR_NA_TYPE", "na requires a resolved numeric type.", node)
        elif isinstance(node, LoadIR):
            binding = bindings.get(node.binding_id)
            if binding is None or owner(binding, scopes) != binding or node.value_type != binding.value_type:
                fail("IR_VARIABLE_SCOPE", "Load refers to an unavailable or shadowed variable.", node)
        elif isinstance(node, ReferenceIR):
            contract = runtime.references.get(node.contract.symbol_id)
            if contract is None or contract != node.contract or node.value_type != contract.value_type or contract.slot not in ("open", "high", "low", "close", "volume", "bar_index") or contract.value_type != ValueType("int" if contract.slot == "bar_index" else "float", "series"):
                fail("IR_REFERENCE", "Reference differs from its admitted context slot.", node)
            if runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE", "Context references require the historical profile.", node)
        elif isinstance(node, HistoryIR):
            visit(node.operand, scopes, depth + 1)
            visit(node.offset, scopes, depth + 1)
            source = node.operand
            expected_id = "reference:" + source.contract.slot if isinstance(source, ReferenceIR) else source.binding_id if isinstance(source, LoadIR) else site_id("expression", source.span, namespace)
            if runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE", "History requires historical execution.", node)
            if isinstance(source, HistoryIR) or source.value_type.base not in ("int", "float", "bool") or source.value_type.qualifier != "series" or node.offset.value_type.base != "int" or node.value_type != source.value_type or node.series_id != expected_id:
                fail("IR_HISTORY", "History source, offset or identity is invalid.", node)
            if isinstance(node.offset, LiteralIR) and not 0 <= node.offset.value <= 100000:
                fail("IR_HISTORY", "Literal history offset is outside the supported range.", node)
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
        elif isinstance(node, BlockIR):
            if node.scope_id not in (branch_scope(node.span, "value:then", namespace), branch_scope(node.span, "value:else", namespace)):
                fail("IR_BLOCK_SCOPE", "Invalid value block scope.", node)
            local_scopes = [*scopes, {}]
            statements(node.statements, local_scopes, node.scope_id, depth + 1)
            visit(node.result, local_scopes, depth + 1)
            expected = ValueType(node.result.value_type.base, dependency_qualifier(node.statements, node.result))
            if expected.base not in SCALARS or node.value_type != expected:
                fail("IR_BLOCK_TYPE", "Invalid value block result.", node)
        elif isinstance(node, UserCallIR):
            function = functions.get(node.function_id)
            expected_site = site_id("user", node.span, namespace)
            if function is None or node.call_site_id != expected_site or expected_site in sites or node.function_id in function_stack:
                fail("IR_USER_CALL", "Invalid function call identity/dependency.", node)
            instances += 1
            if instances > 128 or len(function_stack) >= 12:
                fail("IR_FUNCTION_LIMIT", "Function instance/depth limit exceeded.", node)
            sites.add(expected_site)
            if len(node.parameters) != len(function.parameters) or len(node.arguments) != len(function.parameters):
                fail("IR_FUNCTION_ARGUMENT", "Function argument count differs from definition.", node)
            indices = [a.source_index for a in node.arguments]
            if any(a.origin != "source" for a in node.arguments) or any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(indices))) or node.evaluation_order != tuple(sorted(range(len(indices)), key=lambda i: indices[i])):
                fail("IR_EVALUATION_ORDER", "Invalid function argument evaluation order.", node)
            for argument in node.arguments:
                visit(argument.expression, scopes, depth + 1)
            child_namespace = expected_site + "/"
            child_scope = f"scope:{child_namespace}function"
            parameter_scope = {}
            for specification, parameter, argument in zip(function.parameters, node.parameters, node.arguments, strict=True):
                if bindings.get(parameter.binding_id) != parameter or parameter.binding_id in declarations or parameter.mode != "parameter" or parameter.name != specification.name or parameter.span != specification.span or parameter.namespace != child_namespace or parameter.scope_id != child_scope or argument.parameter_id != parameter.binding_id:
                    fail("IR_FUNCTION_PARAMETER", "Parameter differs from its definition/instance.", node)
                supplied = argument.expression.value_type
                if parameter.value_type.base != supplied.base or (specification.annotation and parameter.value_type.base != specification.annotation) or QUALIFIERS.index(supplied.qualifier) > QUALIFIERS.index(parameter.value_type.qualifier) or (specification.qualifier and parameter.value_type.qualifier != specification.qualifier):
                    fail("IR_FUNCTION_PARAMETER", "Invalid typed function parameter.", node)
                parameter_scope[parameter.name] = parameter
                declarations.add(parameter.binding_id)
            captures = {name: binding for name, binding in root_scope.items() if binding.binding_id in function.captures}
            previous_namespace, previous_stack = namespace, function_stack
            namespace, function_stack = child_namespace, (*function_stack, node.function_id)
            try:
                local_scopes = [captures, parameter_scope]
                statements(node.statements, local_scopes, child_scope, depth + 1)
                visit(node.result, local_scopes, depth + 1)
            finally:
                namespace, function_stack = previous_namespace, previous_stack
            expected = ValueType(node.result.value_type.base, dependency_qualifier(node.statements, node.result, node.arguments, node.parameters))
            if expected.base not in SCALARS or node.value_type != expected:
                fail("IR_FUNCTION_RESULT", "Function result type differs from its body/arguments.", node)
        elif isinstance(node, CallIR):
            contract = runtime.contracts.get(node.overload_id)
            if not contract or any(getattr(node, name) != getattr(contract, name) for name in (
                "symbol_id", "assertion_id", "signature_sha256", "operation", "effects"
            )):
                fail("IR_CALL_CONTRACT", "Call differs from its admitted contract.", node)
            kernel = KERNELS.get(node.operation)
            request=node.operation=="context.request" and node.effects==("requests_context","reads_history")
            if not request and (not kernel or node.effects != kernel.effects):
                fail("IR_CAPABILITY", "Runtime operation/effects are unavailable.", node)
            if request and runtime.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE","Context requests require historical execution.",node)
            if "updates_state" in node.effects and runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE", "Stateful operations require historical execution.", node)
            if node.call_site_id != site_id("call", node.span, namespace) or node.call_site_id in sites:
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
            try:
                expected = call_type(contract, [a.expression.value_type for a in node.arguments])
            except ValueError:
                fail("IR_RESULT_TYPE", "Call arguments do not satisfy its result rule.", node)
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
                if binding.mode == "parameter" or (namespace and binding.namespace != namespace):
                    fail("IR_FUNCTION_WRITE", "Function cannot write parameters or globals.", node)
                visit(node.expression, scopes, depth + 1)
                if node.expression.value_type.base != binding.value_type.base or QUALIFIERS.index(node.expression.value_type.qualifier) > QUALIFIERS.index(binding.value_type.qualifier):
                    fail("IR_STORE_TYPE", "Store value is incompatible with its variable.", node)
                if node.declaration:
                    if binding.namespace != namespace or binding.binding_id in declarations or binding.scope_id != scope_id or binding.name in scopes[-1]:
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
                statements(node.body, [*scopes, {}], branch_scope(node.span, "then", namespace), depth + 1)
                statements(node.otherwise, [*scopes, {}], branch_scope(node.span, "else", namespace), depth + 1)
            elif isinstance(node, CallIR):
                visit(node, scopes, depth + 1, scope_id == "scope:root")
            elif isinstance(node, EvaluateIR):
                limit(node, depth)
                visit(node.expression, scopes, depth + 1)
            else:
                fail("IR_STATEMENT", "Unknown statement node.", node)

    statements(program.statements, [root_scope])
    if declarations != bindings.keys():
        fail("IR_BINDINGS", "Variable table and declarations differ.")
    if not program.statements or not isinstance(program.statements[0], CallIR) or program.statements[0].operation != "harness.indicator" or sum(
        isinstance(node, CallIR) and node.operation == "harness.indicator" for node in program.statements
    ) != 1 or not any(isinstance(node, CallIR) and node.operation == "visual.plot" for node in program.statements):
        fail("IR_PROGRAM_SHAPE", "One declaration followed by global output is required.")
