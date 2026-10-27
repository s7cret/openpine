"""Validate an entire typed program and its lexical scopes before effects."""
from packages.contracts.trading import STRATEGY_DECLARATIONS
from packages.contracts.performance import VARIABLES as TRADE_VARIABLES

import re

from packages.contracts import (
    BinaryIR, CallIR, ConditionalIR, ConvertIR, IfIR, LiteralIR, LoadIR, NAIR,
    ProgramIR, ReferenceIR, StoreIR, UnaryIR, ValueType, VariableContract, HistoryIR,
    HISTORICAL_PROFILE_ID, LITERAL_PROFILE_ID, BlockIR, EvaluateIR, FunctionContract, ParameterContract, UserCallIR,InputIR,
    TupleIR,UnpackIR,TupleType,ForIR,WhileIR,LoopControlIR,ArrayIR,ForInIR,
)
from packages.contracts.arrays import ARRAY_BASES, uses_arrays, validate_operation
from packages.contracts.composite import qualified,uses_composite
from packages.contracts.scopes import branch_scope, dependency_qualifier, site_id
from packages.runtime.language import language_policy
from packages.runtime.operations import KERNELS
from packages.runtime.types import QUALIFIERS, SCALARS, binary_type, call_type, qualifier, valid_type


def admit_program(runtime, program, fail, value_matches, *, expression=False):
    if not isinstance(program, ProgramIR) or program.schema_version not in ("openpine.typed-ir.5","openpine.typed-ir.6","openpine.typed-ir.7","openpine.typed-ir.8","openpine.typed-ir.9","openpine.typed-ir.10","openpine.typed-ir.11"):
        fail("IR_SCHEMA", "Unsupported IR schema.")
    from packages.contracts.prepared_requests import uses_prepared,base_schema
    prepared=uses_prepared(program)
    if prepared!=(program.schema_version=='openpine.typed-ir.11'):fail('IR_SCHEMA','Prepared request targets require typed IR .11.')
    schema=base_schema(program) if prepared else program.schema_version
    from packages.contracts.loops import uses_loops
    from packages.contracts.environment import uses_environment
    environment=uses_environment(program)
    if environment != (schema=='openpine.typed-ir.10'):fail('IR_SCHEMA','Environment requires typed IR .10.')
    if environment and (runtime.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID or f'openpine.environment.v{runtime.identity.language_version}.1' not in runtime.identity.semantic_profile_id.split('+')):fail('IR_PROFILE','Environment profile is not admitted.')
    arrays = uses_arrays(program)
    if not environment and arrays != (schema == 'openpine.typed-ir.9'):fail('IR_SCHEMA', 'Arrays require typed IR .9.')
    if not environment and not arrays and uses_loops(program) != (schema == 'openpine.typed-ir.8'):fail('IR_SCHEMA', 'Loops require typed IR .8.')
    from packages.contracts.inputs import definitions,validate_definition
    input_defs=definitions(program)
    if schema not in ('openpine.typed-ir.7','openpine.typed-ir.8','openpine.typed-ir.9','openpine.typed-ir.10') and bool(input_defs)!=(schema=='openpine.typed-ir.6'):fail('IR_SCHEMA','Input nodes require typed IR .6.')
    if schema not in ('openpine.typed-ir.8','openpine.typed-ir.9','openpine.typed-ir.10') and uses_composite(program)!=(schema=='openpine.typed-ir.7'):fail('IR_SCHEMA','Composite nodes require typed IR .7.')
    if uses_composite(program) and not any(key.startswith('overload.openpine.composite.') for key in runtime.contracts):fail('IR_PROFILE','Composite profile is not admitted.')
    if program.identity != runtime.identity:
        fail("IR_IDENTITY", "Program identity differs from this session.")
    if runtime.identity.implementation_profile_id not in (LITERAL_PROFILE_ID, HISTORICAL_PROFILE_ID):
        fail("IR_PROFILE", "Unknown implementation profile.")
    if arrays and (runtime.identity.language_version < 4 or runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID or f'openpine.arrays.v{runtime.identity.language_version}.1' not in runtime.identity.semantic_profile_id.split('+')):
        fail('IR_PROFILE','Versioned historical array profile is not admitted.')
    policy = language_policy(runtime.identity.language_version)
    from packages.contracts.context_ir import request_nodes
    request_nodes(program)
    from packages.contracts.strategy_ir import strategy_nodes
    strategy_nodes(program)
    if program.language_policy != policy:
        fail("IR_LANGUAGE_POLICY", "Program language policy differs from this runtime.")
    bindings = {}
    for binding in program.bindings:
        if not isinstance(binding, VariableContract) or binding.binding_id in bindings or not valid_type(binding.value_type) or binding.value_type.base not in (*SCALARS,*ARRAY_BASES) or binding.mode not in ("bar", "var", "varip", "parameter", "counter") or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", binding.name):
            fail("IR_BINDING", "Invalid or repeated variable contract.")
        if binding.binding_id != site_id("binding", binding.span, binding.namespace) or (binding.mode in ('var','varip') and binding.value_type.qualifier != "series"):
            fail("IR_BINDING", "Invalid variable identity or persistence qualifier.")
        if binding.mode=='varip' and program.identity.language_version<4:fail('IR_BINDING','varip requires Pine v4 or later.')
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
            if not isinstance(parameter, ParameterContract) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", parameter.name) or parameter.annotation not in (*SCALARS,*ARRAY_BASES, None) or parameter.qualifier not in ("simple", "series", None) or (parameter.qualifier and not parameter.annotation) or not function.span.start <= parameter.span.start < parameter.span.end <= function.span.end:
                fail("IR_FUNCTION", "Invalid function parameter specification.")
        if any(key not in bindings or bindings[key].scope_id != "scope:root" for key in function.captures):
            fail("IR_FUNCTION_CAPTURE", "Function captures must name global bindings.")
        functions[function.function_id] = function
        names.add(function.name)
    sites, declarations = set(), set()
    allowed_input=None
    count = 0
    namespace, function_stack = "", ()
    loop_stack = ()
    instances = 0
    root_scope = {}
    array_element_na = False

    def limit(node, depth):
        nonlocal count
        count += 1
        if count > 8192 or depth > 128:
            fail("IR_LIMIT", "IR size/depth limit exceeded.", node)

    def owner(binding, scopes):
        return next((scope[binding.name] for scope in reversed(scopes) if binding.name in scope), None)

    def visit(node, scopes, depth=0, top_level=False):
        nonlocal namespace, function_stack, instances, loop_stack, array_element_na
        limit(node, depth)
        if not valid_type(getattr(node, "value_type", None)):
            fail("IR_VALUE_TYPE", "Invalid expression type.", node)
        if isinstance(node, ArrayIR):
            expected_site = site_id('array', node.span, namespace)
            if node.call_site_id != expected_site or expected_site in sites:fail('IR_ARRAY_SITE','Invalid array call identity.',node)
            sites.add(expected_site)
            for arg in node.arguments:
                previous = array_element_na
                array_element_na = arg.expression if isinstance(arg.expression, NAIR) and (arg.parameter_id in ('initial_value','value') or node.operation == 'from') else False
                try:visit(arg.expression, scopes, depth + 1)
                finally:array_element_na = previous
            try:validate_operation(node, runtime.identity.language_version)
            except ValueError as error:fail('IR_ARRAY_CONTRACT',str(error),node)
        elif isinstance(node, (ForIR, WhileIR, ForInIR)):
            version = runtime.identity.language_version
            expected_site = site_id('loop', node.span, namespace)
            if (runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID
                    or f'openpine.loops.v{version}.1' not in runtime.identity.semantic_profile_id.split('+')):
                fail('IR_PROFILE', 'Loop policy is not admitted.', node)
            if version < (2 if isinstance(node, ForIR) else 5):fail('IR_LOOP_VERSION', 'Loop is unavailable in this version.', node)
            if node.loop_id != expected_site or expected_site in sites or node.scope_id != branch_scope(node.span, 'loop', namespace):
                fail('IR_LOOP_SCOPE', 'Invalid loop identity/scope.', node)
            sites.add(expected_site)
            local_scopes = [*scopes, {}]
            if isinstance(node, ForIR):
                for child in (node.initial, node.final, node.step):
                    visit(child, scopes, depth + 1)
                    if child.value_type.base != 'int':fail('IR_LOOP_BOUND', 'Loop bounds/step require int.', node)
                counter = node.counter
                if (bindings.get(counter.binding_id) != counter or counter.binding_id in declarations or counter.mode != 'counter'
                        or counter.namespace != namespace or counter.scope_id != node.scope_id or counter.value_type != ValueType('int', 'series')
                        or not node.span.start <= counter.span.start < counter.span.end <= node.span.end):
                    fail('IR_LOOP_COUNTER', 'Invalid loop counter contract.', node)
                declarations.add(counter.binding_id)
                local_scopes[-1][counter.name] = counter
            elif isinstance(node, ForInIR):
                visit(node.collection, scopes, depth + 1)
                if node.collection.value_type.base not in ARRAY_BASES or len(node.targets) not in (1,2):fail('IR_FOR_IN','Invalid array iterator.',node)
                element = node.collection.value_type.base.removeprefix('array.')
                for index, target in enumerate(node.targets):
                    if target is None:continue
                    typ = ValueType('int' if len(node.targets) == 2 and index == 0 else element, 'series')
                    if (bindings.get(target.binding_id) != target or target.binding_id in declarations or target.mode != 'counter'
                            or target.namespace != namespace or target.scope_id != node.scope_id or target.value_type != typ
                            or target.name in local_scopes[-1] or target.name == '_' or not node.span.start <= target.span.start < target.span.end <= node.span.end):
                        fail('IR_FOR_IN_TARGET','Invalid iterator variable.',node)
                    declarations.add(target.binding_id);local_scopes[-1][target.name] = target
            else:
                visit(node.condition, scopes, depth + 1)
                if node.condition.value_type.base != 'bool':fail('IR_LOOP_CONDITION', 'While requires bool.', node)
            previous_loops = loop_stack
            loop_stack = (*loop_stack, expected_site)
            try:
                statements(node.statements, local_scopes, node.scope_id, depth + 1)
                if node.result is not None:
                    visit(node.result, local_scopes, depth + 1)
                    typ = node.result.value_type
                    elements = typ.elements if isinstance(typ, TupleType) else (typ,)
                    if any(t.base not in ('int', 'float', 'bool') for t in elements) or node.value_type != qualified(typ, 'series'):
                        fail('IR_LOOP_RESULT', 'Invalid loop result type.', node)
                elif node.value_type != ValueType('void') or not node.statements:
                    fail('IR_LOOP_RESULT', 'A statement loop requires a nonempty body and void type.', node)
            finally:
                loop_stack = previous_loops
        elif isinstance(node,TupleIR):
            if not 2<=len(node.elements)<=16:fail('IR_TUPLE','Invalid tuple arity.',node)
            for child in node.elements:visit(child,scopes,depth+1)
            if any(child.value_type.base not in SCALARS for child in node.elements):fail('IR_TUPLE','Tuple elements must be scalar.',node)
            expected=qualified(TupleType(tuple(child.value_type for child in node.elements)),qualifier(*(c.value_type for c in node.elements)))
            if node.value_type!=expected:fail('IR_TUPLE','Tuple element types differ.',node)
        elif isinstance(node, LiteralIR):
            if node.value is None or node.value_type.qualifier != "const" or not value_matches(node.value, node.value_type):
                fail("IR_LITERAL_TYPE", "Literal does not match its declared type/range.", node)
        elif isinstance(node, NAIR):
            array_na = arrays and node.value_type.base in ARRAY_BASES and node.value_type.qualifier == 'series'
            string_na = arrays and node is array_element_na and node.value_type == ValueType('string')
            if not array_na and not string_na and (node.value_type.base not in (("int", "float", "bool") if policy.language_version < 6 else ("int", "float")) or node.value_type.qualifier != "const"):
                fail("IR_NA_TYPE", "na requires a resolved numeric type.", node)
        elif isinstance(node,InputIR):
            d=validate_definition(node.definition,program.identity.language_version)
            if (allowed_input is None or node is not allowed_input[1] or namespace or allowed_input[0].name!=d.name
                    or allowed_input[0].scope_id!='scope:root' or allowed_input[0].mode!='bar'
                    or node.value_type!=ValueType('float' if d.input_type=='source' else 'string' if d.input_type in ('symbol','timeframe') else d.input_type,'series' if d.input_type=='source' else 'input')):
                fail('IR_INPUT_SCOPE','Input differs from its direct global declaration.',node)
        elif isinstance(node, LoadIR):
            binding = bindings.get(node.binding_id)
            if binding is None or owner(binding, scopes) != binding or node.value_type != binding.value_type:
                fail("IR_VARIABLE_SCOPE", "Load refers to an unavailable or shadowed variable.", node)
        elif isinstance(node, ReferenceIR):
            contract = runtime.references.get(node.contract.symbol_id)
            if contract is None or contract != node.contract or node.value_type != contract.value_type or (not contract.slot.startswith('environment.') and (contract.slot not in ("open", "high", "low", "close", "volume", "bar_index",'strategy.position_size','strategy.position_avg_price','strategy.equity',*('strategy.'+k for k in TRADE_VARIABLES)) or contract.value_type != ValueType(TRADE_VARIABLES.get(contract.slot.removeprefix('strategy.'),"int" if contract.slot == "bar_index" else "float"), "series"))):
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
            if isinstance(source, HistoryIR) or source.value_type.base not in (*ARRAY_BASES, "int", "float", "bool") or source.value_type.qualifier != "series" or node.offset.value_type.base != "int" or node.value_type != source.value_type or node.series_id != expected_id:
                fail("IR_HISTORY", "History source, offset or identity is invalid.", node)
            if isinstance(node.offset, LiteralIR) and not 0 <= node.offset.value <= 100000:
                fail("IR_HISTORY", "Literal history offset is outside the supported range.", node)
        elif isinstance(node, ConvertIR):
            visit(node.operand, scopes, depth + 1)
            source = node.operand.value_type
            allowed = (source.base == "int" and node.value_type.base == "float") or (
                policy.numeric_conditions and source.base in ("int", "float") and node.value_type.base == "bool")
            tuple_conversion=isinstance(source,TupleType) and isinstance(node.value_type,TupleType)
            if tuple_conversion:
                allowed=(len(source.elements)==len(node.value_type.elements) and all(
                    (a.base==b.base or (a.base,b.base)==('int','float')) and QUALIFIERS.index(a.qualifier)<=QUALIFIERS.index(b.qualifier)
                    for a,b in zip(source.elements,node.value_type.elements,strict=True)))
            if not allowed or not tuple_conversion and node.value_type.qualifier != source.qualifier:
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
            same=(left.base==right.base and left.base in (*SCALARS,*ARRAY_BASES)) or (isinstance(left,TupleType) and isinstance(right,TupleType) and tuple(e.base for e in left.elements)==tuple(e.base for e in right.elements))
            if node.condition.value_type.base != "bool" or not same or node.value_type != qualified(left, qualifier(node.condition.value_type, left, right)):
                fail("IR_CONDITIONAL", "Invalid typed conditional.", node)
        elif isinstance(node, BlockIR):
            if node.scope_id not in (branch_scope(node.span, "value:then", namespace), branch_scope(node.span, "value:else", namespace)):
                fail("IR_BLOCK_SCOPE", "Invalid value block scope.", node)
            local_scopes = [*scopes, {}]
            statements(node.statements, local_scopes, node.scope_id, depth + 1)
            visit(node.result, local_scopes, depth + 1)
            expected = qualified(node.result.value_type, dependency_qualifier(node.statements, node.result))
            if expected.base not in (*SCALARS,*ARRAY_BASES,'tuple') or node.value_type != expected:
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
            previous_namespace, previous_stack, previous_loops = namespace, function_stack, loop_stack
            namespace, function_stack = child_namespace, (*function_stack, node.function_id)
            loop_stack = ()
            try:
                local_scopes = [captures, parameter_scope]
                statements(node.statements, local_scopes, child_scope, depth + 1)
                visit(node.result, local_scopes, depth + 1)
            finally:
                namespace, function_stack, loop_stack = previous_namespace, previous_stack, previous_loops
            expected = qualified(node.result.value_type, dependency_qualifier(node.statements, node.result, node.arguments, node.parameters))
            if expected.base not in (*SCALARS,*ARRAY_BASES,'tuple') or node.value_type != expected:
                fail("IR_FUNCTION_RESULT", "Function result type differs from its body/arguments.", node)
        elif isinstance(node, CallIR):
            contract = runtime.contracts.get(node.overload_id)
            if not contract or any(getattr(node, name) != getattr(contract, name) for name in (
                "symbol_id", "assertion_id", "signature_sha256", "operation", "effects"
            )):
                fail("IR_CALL_CONTRACT", "Call differs from its admitted contract.", node)
            kernel = KERNELS.get(node.operation)
            request=node.operation in ("context.request","context.lower_tf") and node.effects==("requests_context","reads_history")
            from packages.contracts.risk import OPERATIONS as RISK_OPERATIONS
            trading=node.operation in RISK_OPERATIONS or node.operation in ('simulation.strategy','simulation.strategy_lots','simulation.strategy_economics','simulation.strategy_intrabar','simulation.strategy_cycle','simulation.strategy_magnifier','simulation.entry','simulation.entry_pending','simulation.close','simulation.exit','simulation.cancel','simulation.cancel_all','simulation.exit_partial','simulation.close_partial','simulation.order','simulation.entry_group','simulation.exit_group','simulation.close_all','simulation.exit_extended')
            trade_read=node.operation=='trade.read' and node.effects==('reads_trading',)
            if not request and not trading and not trade_read and (not kernel or node.effects != kernel.effects):
                fail("IR_CAPABILITY", "Runtime operation/effects are unavailable.", node)
            if request and runtime.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE","Context requests require historical execution.",node)
            if "updates_state" in node.effects and runtime.identity.implementation_profile_id != HISTORICAL_PROFILE_ID:
                fail("IR_PROFILE", "Stateful operations require historical execution.", node)
            if node.call_site_id != site_id("call", node.span, namespace) or node.call_site_id in sites:
                fail("IR_CALL_SITE", "Invalid or repeated call-site identity.", node)
            sites.add(node.call_site_id)
            if set(node.effects) & {"declaration", "emits_plot", "configures_risk"} and not top_level:
                fail("IR_EFFECT_POSITION", "Declarations and plot calls must be global.", node)
            if tuple(arg.parameter_id for arg in node.arguments) != contract.parameter_ids:
                fail("IR_PARAMETER_IDS", "Bound parameter sequence differs from its signature.", node)
            if node.overload_id.startswith('overload.openpine.signals.'):
                from packages.contracts.signals import validate_intrinsic as validate_signal
                try:validate_signal(node,runtime.identity.language_version)
                except ValueError as error:fail('IR_SIGNAL_INPUT',str(error),node)
            if node.overload_id.startswith('overload.openpine.technical.'):
                from packages.contracts.technical import validate_intrinsic
                try:validate_intrinsic(node,runtime.identity.language_version)
                except ValueError as error:fail('IR_TECHNICAL_INPUT',str(error),node)
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
        nonlocal allowed_input
        for node in nodes:
            if isinstance(node,UnpackIR):
                limit(node,depth);visit(node.expression,scopes,depth+1)
                typ=node.expression.value_type
                if not isinstance(typ,TupleType) or len(node.bindings)!=len(typ.elements):fail('IR_UNPACK','Unpack arity differs.',node)
                for binding,item in zip(node.bindings,typ.elements,strict=True):
                    if binding is None:continue
                    if (bindings.get(binding.binding_id)!=binding or binding.mode!='bar' or binding.name=='_'
                            or binding.namespace!=namespace or binding.scope_id!=scope_id or binding.binding_id in declarations or binding.name in scopes[-1]
                            or binding.value_type.base!=item.base or QUALIFIERS.index(item.qualifier)>QUALIFIERS.index(binding.value_type.qualifier)):
                        fail('IR_UNPACK','Invalid tuple declaration.',node)
                    declarations.add(binding.binding_id);scopes[-1][binding.name]=binding
            elif isinstance(node, StoreIR):
                limit(node, depth)
                binding = bindings.get(node.binding.binding_id)
                if binding != node.binding or type(node.declaration) is not bool:
                    fail("IR_STORE", "Store differs from its variable contract.", node)
                if binding.mode in ("parameter", "counter") or (namespace and binding.namespace != namespace):
                    fail("IR_FUNCTION_WRITE", "Function cannot write parameters or globals.", node)
                previous_input=allowed_input
                input_node=node.expression
                while isinstance(input_node,ConvertIR):input_node=input_node.operand
                allowed_input=(binding,input_node) if node.declaration and scope_id=='scope:root' and isinstance(input_node,InputIR) else None
                try:visit(node.expression, scopes, depth + 1)
                finally:allowed_input=previous_input
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
            elif isinstance(node, LoopControlIR):
                limit(node, depth)
                if node.action not in ('break', 'continue') or not loop_stack or node.loop_id != loop_stack[-1]:
                    fail('IR_LOOP_CONTROL', 'Control must target the nearest loop in this function.', node)
            else:
                fail("IR_STATEMENT", "Unknown statement node.", node)

    statements(program.statements, [root_scope])
    if declarations != bindings.keys():
        fail("IR_BINDINGS", "Variable table and declarations differ.")
    strategy=bool(program.statements and isinstance(program.statements[0],CallIR) and program.statements[0].operation in STRATEGY_DECLARATIONS)
    if not program.statements or not isinstance(program.statements[0], CallIR) or program.statements[0].operation not in ("harness.indicator",'simulation.strategy','simulation.strategy_lots','simulation.strategy_economics','simulation.strategy_intrabar','simulation.strategy_cycle','simulation.strategy_magnifier') or sum(
        isinstance(node, CallIR) and node.operation in ("harness.indicator",'simulation.strategy','simulation.strategy_lots','simulation.strategy_economics','simulation.strategy_intrabar','simulation.strategy_cycle','simulation.strategy_magnifier') for node in program.statements
    ) != 1 or not strategy and not expression and not any(isinstance(node, CallIR) and node.operation == "visual.plot" for node in program.statements):
        fail("IR_PROGRAM_SHAPE", "One declaration followed by global output is required.")
    if expression and (not isinstance(program.statements[-1],EvaluateIR) or program.statements[-1].expression.value_type.base not in ('int','float','bool','tuple')):
        fail('IR_PROGRAM_SHAPE','Private expression requires a final numeric/bool evaluation.')
