"""Bind each source argument once against admitted, scoped machine signatures."""

from __future__ import annotations

from dataclasses import dataclass

from packages.compiler.frontend import parser
from packages.compiler.semantic.specification import Snapshot, SpecError, value_sha
from packages.contracts import CallContract, Diagnostic, FunctionContract, LanguagePolicy, OpenPineError, ParameterContract, ReferenceContract, SourceSpan, ValueType, VariableContract
from packages.contracts.scopes import branch_scope, dependency_qualifier, site_id


@dataclass(frozen=True)
class BoundLiteral:
    value: int | float | bool | str
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundUnary:
    operator: str
    operand: BoundExpression
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundReference:
    contract: ReferenceContract
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundConversion:
    operand: BoundExpression
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundNA:
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundLoad:
    binding: VariableContract
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundBinary:
    operator: str
    left: BoundExpression
    right: BoundExpression
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundConditional:
    condition: BoundExpression
    when_true: BoundExpression
    when_false: BoundExpression
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundHistory:
    operand: BoundExpression
    offset: BoundExpression
    series_id: str
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundStore:
    binding: VariableContract
    expression: BoundExpression
    declaration: bool
    span: SourceSpan


@dataclass(frozen=True)
class BoundIf:
    condition: BoundExpression
    body: tuple[BoundStatement, ...]
    otherwise: tuple[BoundStatement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class BoundArgument:
    parameter_id: str
    expression: BoundExpression
    origin: str
    source_index: int | None


@dataclass(frozen=True)
class BoundCall:
    contract: CallContract
    arguments: tuple[BoundArgument, ...]
    evaluation_order: tuple[int, ...]
    value_type: ValueType
    span: SourceSpan
    namespace: str = ""


@dataclass(frozen=True)
class BoundBlock:
    statements: tuple[BoundStatement, ...]
    result: BoundExpression
    scope_id: str
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundUserCall:
    function_id: str
    call_site_id: str
    parameters: tuple[VariableContract, ...]
    arguments: tuple[BoundArgument, ...]
    evaluation_order: tuple[int, ...]
    statements: tuple[BoundStatement, ...]
    result: BoundExpression
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundEvaluate:
    expression: BoundExpression
    span: SourceSpan


type BoundExpression = BoundLiteral | BoundUnary | BoundReference | BoundConversion | BoundCall | BoundNA | BoundLoad | BoundBinary | BoundConditional | BoundHistory | BoundBlock | BoundUserCall
type BoundStatement = BoundCall | BoundStore | BoundIf | BoundEvaluate


@dataclass
class BindingInfo:
    declaration: parser.Declaration
    scope_id: str
    mutable: bool = False
    value_type: ValueType | None = None
    namespace: str = ""

    def contract(self):
        span = self.declaration.span
        return VariableContract(site_id("binding", span, self.namespace), self.declaration.name, self.value_type,
                                self.declaration.mode, self.scope_id, span, self.namespace)


class Resolver:
    """Resolve lexical owners first, including writes that strengthen qualifiers."""

    def __init__(self, fail, namespace="", in_function=False):
        self.fail = fail
        self.references, self.declarations, self.assignments, self.call_variables = {}, {}, {}, {}
        self.bindings = []
        self.namespace, self.in_function = namespace, in_function
        self.function_captures = {}

    def expression(self, node, scopes):
        if isinstance(node, parser.Reference):
            self.references[id(node)] = next((scope[node.name] for scope in reversed(scopes) if node.name in scope), None)
        elif isinstance(node, parser.Unary):
            self.expression(node.operand, scopes)
        elif isinstance(node, parser.History):
            self.expression(node.operand, scopes)
            self.expression(node.offset, scopes)
            info = self.references.get(id(node.operand))
            if info is not None:
                info.mutable = True  # A referenced variable needs a per-bar cell even for constant initialization.
        elif isinstance(node, parser.Binary):
            self.expression(node.left, scopes)
            self.expression(node.right, scopes)
        elif isinstance(node, parser.Conditional):
            for child in (node.condition, node.when_true, node.when_false):
                self.expression(child, scopes)
        elif isinstance(node, parser.ValueIf):
            self.expression(node.condition, scopes)
            self.statements(node.body, [*scopes, {}], branch_scope(node.span, "value:then", self.namespace), True)
            self.statements(node.otherwise, [*scopes, {}], branch_scope(node.span, "value:else", self.namespace), True)
        elif isinstance(node, parser.Call):
            self.call_variables[id(node)] = any(node.name in scope for scope in scopes)
            for argument in node.arguments:
                self.expression(argument.expression, scopes)

    def statements(self, statements, scopes=None, scope_id="scope:root", value_tail=False):
        scopes = [{}] if scopes is None else scopes
        for node in statements:
            if isinstance(node, parser.Function):
                if scope_id != "scope:root" or self.in_function:
                    self.fail("LANGUAGE_INVALID", "FUNCTION_GLOBAL_ONLY", "Function definitions must be global.", node.span)
                self.function_captures[node.name] = dict(scopes[0])
            elif isinstance(node, parser.ExpressionStatement):
                self.expression(node.expression, scopes)
            elif isinstance(node, parser.Declaration):
                if node.name in scopes[-1]:
                    self.fail("LANGUAGE_INVALID", "DUPLICATE_VARIABLE", f"Variable {node.name!r} already exists in this scope.", node.span, "rule.scope")
                self.expression(node.expression, scopes)
                info = BindingInfo(node, scope_id, node.mode == "var", namespace=self.namespace)
                scopes[-1][node.name] = info
                self.declarations[id(node)] = info
                self.bindings.append(info)
            elif isinstance(node, parser.Reassignment):
                info = next((scope[node.name] for scope in reversed(scopes) if node.name in scope), None)
                if info is None:
                    self.fail("LANGUAGE_INVALID", "ASSIGN_UNDEFINED", f"Cannot assign undeclared variable {node.name!r}.", node.span, "rule.scope")
                if self.in_function and (not info.namespace or info.declaration.mode == "parameter"):
                    self.fail("LANGUAGE_INVALID", "FUNCTION_WRITE", "Functions cannot reassign global variables or parameters.", node.span, "rule.scope")
                info.mutable = True
                self.assignments[id(node)] = info
                self.expression(node.expression, scopes)
            elif isinstance(node, parser.If):
                if value_tail and node is statements[-1]:
                    self.expression(parser.ValueIf(node.condition, node.body, node.otherwise, node.span), scopes)
                    continue
                self.expression(node.condition, scopes)
                self.statements(node.body, [*scopes, {}], branch_scope(node.span, "then", self.namespace))
                self.statements(node.otherwise, [*scopes, {}], branch_scope(node.span, "else", self.namespace))
            else:
                self.expression(node, scopes)


def literal_type(value) -> ValueType:
    return ValueType({int: "int", float: "float", bool: "bool", str: "string"}[type(value)])


def type_options(description: dict) -> tuple[str, ...]:
    if description["kind"] == "primitive":
        return (description["name"],)
    if description["kind"] == "alternative":
        return tuple(name for option in description["options"] for name in type_options(option))
    raise SpecError("IMPLEMENTATION_MISSING", "Parameter type form is not implemented in the literal slice")


class Catalog:
    def __init__(self, snapshot: Snapshot, capabilities: dict[str, tuple[tuple[str, ...], ...]], reference_slots=None, language_version=6):
        self.snapshot, self.capabilities = snapshot, capabilities
        self.language_version = language_version
        self.policy = LanguagePolicy(**snapshot.language_policy(language_version))
        registry = snapshot.registry_for(language_version)
        self.rules = {}
        rules = {row["rule_id"]: row for row in registry["rules"]}
        for rule_id in ("rule.binding", "rule.overload", "rule.types", "rule.grammar", "rule.arguments.left_to_right"):
            rule = rules.get(rule_id)
            if not rule:
                raise SpecError("SPEC_UNRESOLVED", f"Required compiler rule is missing: {rule_id}")
            self.rules[rule_id] = snapshot.resolve(rule["semantic_assertion"], language_version)
        self.signatures = {}
        self.contracts = {}
        self.references = {}
        for symbol in registry["symbols"]:
            if language_version not in symbol["language_versions"]:
                continue
            if symbol["name"] in (reference_slots or {}):
                assertion_id = symbol["value_assertion_ref"]
                value = snapshot.resolve(assertion_id, language_version)
                self.references[symbol["name"]] = ReferenceContract(
                    symbol["symbol_id"], assertion_id, value_sha(value), reference_slots[symbol["name"]],
                    ValueType(type_options(value["type"])[0], value["qualifier"]))
            for overload in symbol["overloads"]:
                assertion_id = overload["signature_assertion"]
                try:
                    signature = snapshot.resolve(assertion_id, language_version)
                except SpecError as error:
                    if error.code in ("SPEC_UNRESOLVED", "PROFILE_UNCOVERED"):
                        continue
                    raise
                contract = None
                parameters = signature["parameters"]
                names = tuple(parameter["name"] for parameter in parameters)
                if names in self.capabilities.get(signature["operation"], ()):
                    if any(parameter["variadic"] or parameter["evaluation_policy"] != "eager" for parameter in parameters):
                        raise SpecError("IMPLEMENTATION_MISSING", "Kernel does not support this argument evaluation contract")
                    result = signature["result"]
                    if result["kind"] not in ("void", "dependent", "scalar"):
                        raise SpecError("IMPLEMENTATION_MISSING", "Kernel result contract is not implemented")
                    result_type = ValueType("void")
                    result_rule = "fixed"
                    if result["kind"] == "scalar":
                        if result["qualifier"]["relation"] == "derived":
                            result_rule = "derived"
                        elif result["qualifier"]["relation"] != "exact":
                            raise SpecError("IMPLEMENTATION_MISSING", "Scalar result qualifier is not implemented")
                        result_type = ValueType(type_options(result["type"])[0], result["qualifier"].get("value", "const"))
                    contract = CallContract(
                        symbol["symbol_id"], overload["overload_id"], assertion_id, value_sha(signature),
                        signature["operation"], tuple(parameter["parameter_id"] for parameter in parameters),
                        tuple(type_options(parameter["type"]) for parameter in parameters),
                        tuple(parameter["qualifier_constraint"]["value"] for parameter in parameters),
                        names.index(result["parameter"]) if result["kind"] == "dependent" else None,
                        tuple(signature["effects"]),
                        result_type,
                        result_rule,
                    )
                    if any(parameter["qualifier_constraint"]["relation"] != "at_most" for parameter in parameters):
                        raise SpecError("IMPLEMENTATION_MISSING", "Qualifier relation is not implemented")
                    self.contracts[contract.overload_id] = contract
                self.signatures.setdefault(symbol["name"], []).append((signature, contract))
        self.install_series(snapshot.series, reference_slots or {})
        from .requests import install
        install(self)
        from .strategies import install as install_strategies
        install_strategies(self)

    def install_series(self, release, reference_slots):
        if not release:
            return
        row = release["versions"][str(self.language_version)]
        for reference in row["references"]:
            name = reference["name"]
            if name in reference_slots:
                identity = f"openpine.series.v{self.language_version}.reference.{name}"
                self.references[name] = ReferenceContract("symbol." + identity, "assertion." + identity,
                    value_sha(reference), reference_slots[name], ValueType(reference["base"], "series"))
        replaced = set()
        for api in row["apis"]:
            name = api["name"]
            if name not in replaced:
                self.signatures[name] = []
                replaced.add(name)
            oid = "overload." + api["id"]
            parameters = [{"name": p["name"], "parameter_id": f"parameter.{oid}.{p['name']}",
                "requiredness": {"state": "required" if p["default"] is None else "optional"},
                "default": p["default"] or {"kind": "none"}} for p in api["parameters"]]
            signature = {"parameters": parameters, "binding": {"named": api["named"]}, "effects": api["effects"]}
            names = tuple(p["name"] for p in parameters)
            contract = None
            if names in self.capabilities.get(api["operation"], ()):
                contract = CallContract(f"symbol.function.openpine.series.v{self.language_version}.{name}", oid,
                    "assertion." + api["id"], value_sha(api), api["operation"],
                    tuple(p["parameter_id"] for p in parameters), tuple(tuple(p["types"]) for p in api["parameters"]),
                    tuple(p["qualifier"] for p in api["parameters"]), None, tuple(api["effects"]),
                    ValueType(api["result"]), api["result_rule"])
                self.contracts[oid] = contract
            self.signatures[name].append((signature, contract))


class Binder:
    def __init__(self, catalog: Catalog, shared=None, namespace="", stack=()):
        self.catalog = catalog
        self.qualifiers = catalog.rules["rule.types"]["qualifier_order"]
        self.namespace, self.stack = namespace, stack
        self.resolver = Resolver(self.fail, namespace, bool(stack))
        self.shared = shared if shared is not None else {"functions": {}, "captures": {}, "bindings": [], "instances": 0, "nodes": 0, "depth": 0}

    def limit(self, span):
        self.shared["nodes"] += 1
        if self.shared["nodes"] > 8192:
            self.fail("PROFILE_UNCOVERED", "FUNCTION_EXPANSION", "Expanded program exceeds 8192 nodes.", span)

    def qualifier(self, *expressions):
        return max((expression.value_type.qualifier for expression in expressions), key=self.qualifiers.index)

    def coerce(self, expression, base, code="ASSIGNMENT_TYPE"):
        if expression.value_type.base == base:
            return expression
        target = ValueType(base, expression.value_type.qualifier)
        if isinstance(expression, BoundNA) and (base in ("int", "float") or (base == "bool" and self.catalog.language_version < 6)):
            return BoundNA(target, expression.span)
        if expression.value_type.base == "int" and base == "float":
            return BoundConversion(expression, target, expression.span)
        self.fail("LANGUAGE_INVALID", code, f"Cannot use {expression.value_type.base} as {base}.", expression.span, "rule.types")

    def condition(self, expression):
        if expression.value_type.base == "bool":
            return expression
        if self.catalog.policy.numeric_conditions and expression.value_type.base in ("int", "float"):
            return BoundConversion(expression, ValueType("bool", expression.value_type.qualifier), expression.span)
        self.fail("LANGUAGE_INVALID", "CONDITION_TYPE", "Condition requires a boolean value.", expression.span, "rule.types")

    def common(self, left, right, span):
        bases = {left.value_type.base, right.value_type.base}
        if bases <= {"int", "float", "na"}:
            base = "float" if "float" in bases else "int" if "int" in bases else "float"
            return self.coerce(left, base), self.coerce(right, base), base
        if len(bases) == 1 and bases <= {"bool", "string"}:
            return left, right, left.value_type.base
        if bases <= {"bool", "na"} and self.catalog.language_version < 6:
            return self.coerce(left, "bool"), self.coerce(right, "bool"), "bool"
        self.fail("LANGUAGE_INVALID", "EXPRESSION_TYPE", "Expression operands have incompatible types.", span, "rule.types")

    def fail(self, category: str, code: str, message: str, span: SourceSpan, rule="rule.binding"):
        raise OpenPineError(Diagnostic(category, code, message, span, rule, self.catalog.language_version))

    def expression(self, expression: parser.Expression, top_level=False) -> BoundExpression:
        self.shared["depth"] += 1
        try:
            if self.shared["depth"] > 128:
                self.fail("PROFILE_UNCOVERED", "FUNCTION_EXPANSION", "Expanded expression nesting exceeds 128.", expression.span)
            return self._expression(expression, top_level)
        finally:
            self.shared["depth"] -= 1

    def _expression(self, expression: parser.Expression, top_level=False) -> BoundExpression:
        self.limit(expression.span)
        if isinstance(expression, parser.Literal):
            if expression.value is None:
                return BoundNA(ValueType("na"), expression.span)
            return BoundLiteral(expression.value, literal_type(expression.value), expression.span)
        if isinstance(expression, parser.Reference):
            info = self.resolver.references.get(id(expression))
            if info is not None:
                if info.value_type is None:
                    self.fail("LANGUAGE_INVALID", "FUNCTION_CAPTURE_ORDER", "A function capture must be initialized before the call.", expression.span, "rule.scope")
                return BoundLoad(info.contract(), info.value_type, expression.span)
            if expression.name in self.catalog.constants:
                value=self.catalog.constants[expression.name]
                return BoundLiteral(value,literal_type(value),expression.span)
            contract = self.catalog.references.get(expression.name)
            if contract is None:
                known = any(row["name"] == expression.name and row["category"] == "variables" for row in self.catalog.snapshot.references[self.catalog.language_version]["entries"])
                self.fail("PROFILE_UNCOVERED" if known else "LANGUAGE_INVALID", "VALUE_REFERENCE" if known else "UNDEFINED_VARIABLE", f"No available variable {expression.name!r}.", expression.span, "rule.scope")
            return BoundReference(contract, contract.value_type, expression.span)
        if isinstance(expression, parser.Unary):
            operand = self.expression(expression.operand)
            if expression.operator == "not":
                operand = self.condition(operand)
                return BoundUnary("not", operand, operand.value_type, expression.span)
            if operand.value_type.base not in ("int", "float"):
                self.fail("LANGUAGE_INVALID", "UNARY_TYPE", "Unary signs require a numeric value.", expression.span, "rule.types")
            return BoundUnary(expression.operator, operand, operand.value_type, expression.span)
        if isinstance(expression, parser.History):
            if isinstance(expression.operand, parser.History):
                self.fail("LANGUAGE_INVALID", "HISTORY_CHAIN", "Repeated history indexing is not allowed.", expression.span, "rule.history")
            operand, offset = self.expression(expression.operand), self.expression(expression.offset)
            if operand.value_type.base not in ("int", "float", "bool") or operand.value_type.qualifier != "series":
                self.fail("LANGUAGE_INVALID", "HISTORY_SOURCE", "History requires a numeric/bool series.", expression.span, "rule.history")
            if offset.value_type.base != "int":
                self.fail("LANGUAGE_INVALID", "HISTORY_OFFSET_TYPE", "History offset requires int.", offset.span, "rule.history")
            constant = None
            if isinstance(offset, BoundLiteral):
                constant = offset.value
            elif isinstance(offset, BoundUnary) and offset.operator in ("+", "-") and isinstance(offset.operand, BoundLiteral):
                constant = -offset.operand.value if offset.operator == "-" else offset.operand.value
            if constant is not None and not 0 <= constant <= 100000:
                self.fail("LANGUAGE_INVALID", "HISTORY_OFFSET", "History offset must be 0..100000.", offset.span, "rule.history")
            if isinstance(operand, BoundReference):
                sid = "reference:" + operand.contract.slot
            elif isinstance(operand, BoundLoad):
                sid = operand.binding.binding_id
            else:
                sid = site_id("expression", operand.span, self.namespace)
            return BoundHistory(operand, offset, sid, ValueType(operand.value_type.base, "series"), expression.span)
        if isinstance(expression, parser.Binary):
            left, right = self.expression(expression.left), self.expression(expression.right)
            operator = expression.operator
            if operator in ("and", "or"):
                left, right = self.condition(left), self.condition(right)
                base = "bool"
            else:
                left, right, base = self.common(left, right, expression.span)
                if operator in ("==", "!=", "<", "<=", ">", ">="):
                    if operator not in ("==", "!=") and base not in ("int", "float"):
                        self.fail("LANGUAGE_INVALID", "ORDERING_TYPE", "Ordering requires numeric operands.", expression.span, "rule.types")
                    base = "bool"
                elif base not in ("int", "float") and not (operator == "+" and base == "string"):
                    self.fail("LANGUAGE_INVALID", "ARITHMETIC_TYPE", "Arithmetic requires numeric operands.", expression.span, "rule.types")
                elif operator == "/" and (base == "float" or self.catalog.policy.integer_division == "float"):
                    base = "float"
            return BoundBinary(operator, left, right, ValueType(base, self.qualifier(left, right)), expression.span)
        if isinstance(expression, parser.Conditional):
            condition = self.condition(self.expression(expression.condition))
            left, right, base = self.common(self.expression(expression.when_true), self.expression(expression.when_false), expression.span)
            return BoundConditional(condition, left, right, ValueType(base, self.qualifier(condition, left, right)), expression.span)
        if isinstance(expression, parser.ValueIf):
            return self.value_if(expression)
        if self.resolver.call_variables.get(id(expression)):
            self.fail("LANGUAGE_INVALID", "CALL_VARIABLE", "A variable cannot be called as a function.", expression.span, "rule.scope")
        if expression.name in self.shared["functions"]:
            return self.user_call(expression)
        candidates = self.catalog.signatures.get(expression.name)
        if not candidates:
            known = any(row["name"] == expression.name for row in self.catalog.snapshot.references[self.catalog.language_version]["entries"])
            self.fail("SPEC_UNRESOLVED" if known else "LANGUAGE_INVALID", "SIGNATURE_UNAVAILABLE",
                      f"No admitted signature for {expression.name}.", expression.span)
        implemented = [(signature, contract) for signature, contract in candidates if contract]
        if not implemented:
            self.fail("IMPLEMENTATION_MISSING", "OPERATION_UNAVAILABLE",
                      f"{expression.name} is outside this implementation profile's runtime capabilities.", expression.span)
        # Bind nested source expressions once, independent of overload selection.
        if len(implemented)==1 and implemented[0][1].operation=="context.request":
            from .requests import bind
            return bind(self,expression,*implemented[0],top_level)
        if len(implemented)==1 and implemented[0][1].operation.startswith('simulation.'):
            from .strategies import bind
            return bind(self,expression,*implemented[0],top_level)
        arguments = tuple(self.expression(argument.expression) for argument in expression.arguments)
        matches, errors = [], []
        for signature, contract in implemented:
            try:
                matches.append(self.call(expression, arguments, signature, contract, top_level))
            except OpenPineError as error:
                errors.append(error)
        if len(matches) > 1:
            self.fail("LANGUAGE_INVALID", "AMBIGUOUS_OVERLOAD", "Multiple overloads match.", expression.span, "rule.overload")
        if not matches:
            if len(errors) == 1:
                raise errors[0]
            self.fail("LANGUAGE_INVALID", "NO_MATCHING_OVERLOAD", "No overload matches the arguments.", expression.span, "rule.overload")
        return matches[0]

    def call(self, call: parser.Call, expressions: tuple[BoundExpression, ...], signature: dict,
             contract: CallContract, top_level: bool) -> BoundCall:
        if set(contract.effects) & {"declaration", "emits_plot"} and not top_level:
            self.fail("PROFILE_UNCOVERED", "NESTED_EFFECT", "Declaration and plot handles cannot be used as values in this slice.", call.span)
        parameters = signature["parameters"]
        positions = {parameter["name"]: position for position, parameter in enumerate(parameters)}
        bound = {}
        positional = 0
        named_seen = False
        for source_index, (argument, expression) in enumerate(zip(call.arguments, expressions, strict=True)):
            if argument.name is not None:
                named_seen = True
                if not signature["binding"]["named"]:
                    self.fail("PROFILE_UNCOVERED", "NAMED_ARGUMENT_UNVERIFIED", "Named calls are not established for this historical signature.", argument.span)
                if argument.name not in positions:
                    self.fail("LANGUAGE_INVALID", "UNKNOWN_ARGUMENT", f"Unknown argument {argument.name!r}.", argument.span)
                position = positions[argument.name]
            else:
                if named_seen:
                    self.fail("LANGUAGE_INVALID", "POSITIONAL_AFTER_NAMED", "Positional arguments must precede named arguments.", argument.span)
                position = positional
                positional += 1
            if position >= len(parameters):
                self.fail("LANGUAGE_INVALID", "TOO_MANY_ARGUMENTS", "Too many arguments.", argument.span)
            if position in bound:
                self.fail("LANGUAGE_INVALID", "DUPLICATE_ARGUMENT", "Argument supplied more than once.", argument.span)
            bound[position] = BoundArgument(parameters[position]["parameter_id"], expression, "source", source_index)
        for position, parameter in enumerate(parameters):
            if position not in bound:
                if parameter["requiredness"]["state"] == "required":
                    self.fail("LANGUAGE_INVALID", "MISSING_ARGUMENT", f"Missing argument {parameter['name']!r}.", call.span)
                default = parameter["default"]
                if default["kind"] == "typed_zero":
                    source_type = bound[0].expression.value_type.base
                    value = False if source_type == "bool" else 0.0 if source_type in ("float", "na") else 0
                elif default["kind"] == "literal":
                    value = default["value"]
                elif default["kind"] == "na":
                    bound[position]=BoundArgument(parameter['parameter_id'],BoundNA(ValueType('na'),call.span),'default',None)
                    continue
                else:
                    self.fail("SPEC_UNRESOLVED", "DEFAULT_UNAVAILABLE", "No usable literal default.", call.span)
                bound[position] = BoundArgument(parameter["parameter_id"],
                    BoundLiteral(value, literal_type(value), call.span), "default", None)
        if contract.result_rule == "common":
            common = bound[0].expression
            for position in range(1, len(parameters)):
                _, _, base = self.common(common, bound[position].expression, call.span)
                common = self.coerce(common, base)
            for position, previous in list(bound.items()):
                bound[position] = BoundArgument(previous.parameter_id,
                    self.coerce(previous.expression, common.value_type.base), previous.origin, previous.source_index)
        for position, parameter in enumerate(parameters):
            value_type = bound[position].expression.value_type
            if value_type.base == "na":
                numeric = [base for base in contract.parameter_types[position] if base in ("int", "float")]
                if numeric:
                    previous = bound[position]
                    converted = self.coerce(previous.expression, "float" if "float" in numeric else numeric[0])
                    bound[position] = BoundArgument(previous.parameter_id, converted, previous.origin, previous.source_index)
                    value_type = converted.value_type
            if value_type.base == "int" and "float" in contract.parameter_types[position] and "int" not in contract.parameter_types[position] and ["int", "float"] in self.catalog.rules["rule.types"]["implicit_conversions"]:
                previous = bound[position]
                promoted = BoundConversion(previous.expression, ValueType("float", value_type.qualifier), previous.expression.span)
                bound[position] = BoundArgument(previous.parameter_id, promoted, previous.origin, previous.source_index)
                value_type = promoted.value_type
            if value_type.base not in contract.parameter_types[position]:
                self.fail("LANGUAGE_INVALID", "ARGUMENT_TYPE", f"Invalid type for {parameter['name']!r}.",
                          bound[position].expression.span, "rule.types")
            if self.qualifiers.index(value_type.qualifier) > self.qualifiers.index(contract.qualifier_limits[position]):
                self.fail("LANGUAGE_INVALID", "ARGUMENT_QUALIFIER", "Invalid argument qualifier.", call.span, "rule.types")
        arguments = tuple(bound[position] for position in range(len(parameters)))
        order = tuple(sorted(range(len(arguments)), key=lambda position: (
            arguments[position].origin == "default",
            arguments[position].source_index if arguments[position].source_index is not None else position,
        )))
        result_type = arguments[contract.result_parameter].expression.value_type if contract.result_parameter is not None else contract.result_type
        if contract.result_rule in ("derived", "common"):
            base = arguments[0].expression.value_type.base if contract.result_rule == "common" else contract.result_type.base
            result_type = ValueType(base, self.qualifier(*(a.expression for a in arguments)))
        elif contract.result_rule in ("fixed_series", "source_series"):
            result_type = ValueType(arguments[0].expression.value_type.base if contract.result_rule == "source_series" else contract.result_type.base, "series")
        return BoundCall(contract, arguments, order, result_type, call.span, self.namespace)

    def value_block(self, nodes, scope_id, span):
        if not nodes:
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "A value block requires a final expression.", span)
        prefix = self.statements(nodes[:-1], scope_id)
        tail = nodes[-1]
        if isinstance(tail, parser.If):
            result = self.value_if(parser.ValueIf(tail.condition, tail.body, tail.otherwise, tail.span))
        elif isinstance(tail, parser.ExpressionStatement):
            result = self.expression(tail.expression)
        elif isinstance(tail, parser.Call):
            result = self.expression(tail)
        else:
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "The final statement must return a scalar expression.", tail.span)
        if result.value_type.base == "void":
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "A value block cannot return void.", tail.span)
        return BoundBlock(prefix, result, scope_id, ValueType(result.value_type.base, dependency_qualifier(prefix, result)), span)

    def value_if(self, node):
        condition = self.condition(self.expression(node.condition))
        left = self.value_block(node.body, branch_scope(node.span, "value:then", self.namespace), node.span)
        if node.otherwise:
            right = self.value_block(node.otherwise, branch_scope(node.span, "value:else", self.namespace), node.span)
        else:
            base = left.value_type.base
            if base == "string":
                self.fail("PROFILE_UNCOVERED", "STRING_BRANCH_REQUIRED", "String-valued if requires else.", node.span)
            missing = BoundLiteral(False, ValueType("bool"), node.span) if base == "bool" and self.catalog.language_version == 6 else BoundNA(ValueType(base), node.span)
            right = BoundBlock((), missing, branch_scope(node.span, "value:else", self.namespace), missing.value_type, node.span)
        a, b, base = self.common(left.result, right.result, node.span)
        left = BoundBlock(left.statements, a, left.scope_id, ValueType(a.value_type.base, dependency_qualifier(left.statements, a)), left.span)
        right = BoundBlock(right.statements, b, right.scope_id, ValueType(b.value_type.base, dependency_qualifier(right.statements, b)), right.span)
        return BoundConditional(condition, left, right, ValueType(base, self.qualifier(condition, left, right)), node.span)

    def user_call(self, call):
        definition = self.shared["functions"][call.name]
        if call.name in self.stack:
            self.fail("LANGUAGE_INVALID", "FUNCTION_RECURSION", "Recursive function calls are not allowed.", call.span)
        self.shared["instances"] += 1
        if len(self.stack) >= 12 or self.shared["instances"] > 128:
            self.fail("PROFILE_UNCOVERED", "FUNCTION_LIMIT", "Function depth/instance limit exceeded.", call.span)
        parameters = definition.parameters
        positions = {p.name: i for i, p in enumerate(parameters)}
        supplied, named_seen = {}, False
        for i, argument in enumerate(call.arguments):
            if argument.name is not None:
                named_seen = True
                if argument.name not in positions:
                    self.fail("LANGUAGE_INVALID", "UNKNOWN_ARGUMENT", "Unknown function argument.", argument.span)
                position = positions[argument.name]
            else:
                if named_seen:
                    self.fail("LANGUAGE_INVALID", "POSITIONAL_AFTER_NAMED", "Positional arguments must precede named arguments.", argument.span)
                position = i
            if position >= len(parameters):
                self.fail("LANGUAGE_INVALID", "TOO_MANY_ARGUMENTS", "Too many function arguments.", argument.span)
            if position in supplied:
                self.fail("LANGUAGE_INVALID", "DUPLICATE_ARGUMENT", "Function argument supplied twice.", argument.span)
            supplied[position] = (self.expression(argument.expression), i)
        if len(supplied) != len(parameters):
            self.fail("LANGUAGE_INVALID", "MISSING_ARGUMENT", "All function parameters are required.", call.span)
        call_id = site_id("user", call.span, self.namespace)
        namespace = call_id + "/"
        scope_id = f"scope:{namespace}function"
        child = Binder(self.catalog, self.shared, namespace, (*self.stack, call.name))
        globals_ = self.shared["captures"][call.name]
        local = {}
        for parameter in parameters:
            declaration = parser.Declaration(parameter.name, parameter.annotation, "parameter", None, parameter.span)
            info = BindingInfo(declaration, scope_id, namespace=namespace)
            local[parameter.name] = info
            child.resolver.bindings.append(info)
        child.resolver.statements(definition.body, [globals_, local], scope_id, True)
        arguments, contracts = [], []
        for position, parameter in enumerate(parameters):
            expression, index = supplied[position]
            if parameter.annotation:
                expression = self.coerce(expression, parameter.annotation, "FUNCTION_ARGUMENT_TYPE")
            if expression.value_type.base not in ("int", "float", "bool", "string"):
                self.fail("LANGUAGE_INVALID", "FUNCTION_ARGUMENT_TYPE", "Function parameters require resolved scalar types.", call.span)
            info = local[parameter.name]
            qualifier = "series" if info.mutable else parameter.qualifier or expression.value_type.qualifier
            if parameter.qualifier == "simple" and (qualifier == "series" or self.qualifiers.index(expression.value_type.qualifier) > self.qualifiers.index("simple")):
                self.fail("LANGUAGE_INVALID", "FUNCTION_ARGUMENT_QUALIFIER", "Simple function parameter cannot receive series values/history.", call.span)
            info.value_type = ValueType(expression.value_type.base, qualifier)
            contract = info.contract()
            contracts.append(contract)
            arguments.append(BoundArgument(contract.binding_id, expression, "source", index))
        block = child.value_block(definition.body, scope_id, definition.span)
        if block.value_type.base == "na":
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "Function return type cannot be inferred from bare na.", definition.span)
        self.shared["bindings"].extend(child.resolver.bindings)
        result_type = ValueType(block.value_type.base, dependency_qualifier(block.statements, block.result, tuple(arguments), tuple(contracts)))
        return BoundUserCall(site_id("function", definition.span), call_id, tuple(contracts), tuple(arguments),
            tuple(sorted(range(len(arguments)), key=lambda i: arguments[i].source_index)), block.statements, block.result, result_type, call.span)

    def statements(self, statements, scope_id="scope:root"):
        bound = []
        for node in statements:
            self.limit(node.span)
            if isinstance(node, parser.Function):
                continue
            if isinstance(node, parser.ExpressionStatement):
                bound.append(BoundEvaluate(self.expression(node.expression), node.span))
            elif isinstance(node, parser.Declaration):
                info = self.resolver.declarations[id(node)]
                value = self.expression(node.expression)
                if value.value_type.base == "void":
                    self.fail("LANGUAGE_INVALID", "VOID_ASSIGNMENT", "A void expression cannot initialize a variable.", node.span, "rule.types")
                if node.annotation:
                    value = self.coerce(value, node.annotation)
                elif value.value_type.base == "na":
                    self.fail("LANGUAGE_INVALID", "UNTYPED_NA", "A na initializer requires an explicit numeric type.", node.span, "rule.types")
                info.value_type = ValueType(value.value_type.base, "series" if info.mutable else value.value_type.qualifier)
                bound.append(BoundStore(info.contract(), value, True, node.span))
            elif isinstance(node, parser.Reassignment):
                info = self.resolver.assignments[id(node)]
                value = self.coerce(self.expression(node.expression), info.value_type.base)
                bound.append(BoundStore(info.contract(), value, False, node.span))
            elif isinstance(node, parser.If):
                condition = self.condition(self.expression(node.condition))
                body = self.statements(node.body, branch_scope(node.span, "then", self.namespace))
                otherwise = self.statements(node.otherwise, branch_scope(node.span, "else", self.namespace))
                bound.append(BoundIf(condition, body, otherwise, node.span))
            else:
                if scope_id != "scope:root" and any(signature["effects"] and set(signature["effects"]) & {"declaration", "emits_plot"} for signature, _ in self.catalog.signatures.get(node.name, ())):
                    self.fail("LANGUAGE_INVALID", "GLOBAL_ONLY_CALL", "Declaration and plot calls must be global.", node.span, "rule.scope")
                expression = self.expression(node, top_level=scope_id == "scope:root")
                bound.append(BoundEvaluate(expression, node.span) if isinstance(expression, BoundUserCall) else expression)
        return tuple(bound)

    @property
    def bindings(self):
        return tuple(info.contract() for info in (*self.resolver.bindings, *self.shared["bindings"]))

    @property
    def functions(self):
        return tuple(FunctionContract(site_id("function", node.span), name,
            tuple(ParameterContract(p.name, p.annotation, p.qualifier, p.span) for p in node.parameters),
            tuple(info.contract().binding_id for info in self.shared["captures"][name].values()), node.span)
            for name, node in self.shared["functions"].items())

    def program(self, statements: tuple[parser.Statement, ...], empty_span: SourceSpan) -> tuple[BoundStatement, ...]:
        for node in statements:
            if isinstance(node, parser.Function):
                if node.name in self.shared["functions"]:
                    self.fail("LANGUAGE_INVALID", "DUPLICATE_FUNCTION", "Function name is already defined.", node.span)
                if node.name in self.catalog.signatures or node.name in self.catalog.references or node.name in {"if", "else", "var", "na", "true", "false"}:
                    self.fail("LANGUAGE_INVALID", "FUNCTION_NAME", "Function name conflicts with a builtin.", node.span)
                if len(node.parameters) > 16 or len(self.shared["functions"]) >= 64:
                    self.fail("PROFILE_UNCOVERED", "FUNCTION_LIMIT", "Function definition/parameter limit exceeded.", node.span)
                if len({p.name for p in node.parameters}) != len(node.parameters):
                    self.fail("LANGUAGE_INVALID", "DUPLICATE_PARAMETER", "Function parameters must be unique.", node.span)
                self.shared["functions"][node.name] = node
        self.resolver.statements(statements)
        self.shared["captures"] = self.resolver.function_captures
        self.validate_functions()
        bound = self.statements(statements)
        declarations = [statement for statement in bound if isinstance(statement, BoundCall) and "declaration" in statement.contract.effects]
        span = statements[0].span if statements else empty_span
        if len(declarations) != self.catalog.rules["rule.grammar"]["program_declaration_count"]:
            self.fail("LANGUAGE_INVALID", "DECLARATION_COUNT", "Exactly one indicator declaration is required.", span, "rule.grammar")
        if bound[0] is not declarations[0]:
            self.fail("PROFILE_UNCOVERED", "DECLARATION_ORDER", "This slice requires the declaration before output calls.", span, "rule.grammar")
        if declarations[0].contract.operation!='simulation.strategy' and not any(isinstance(statement, BoundCall) and "emits_plot" in statement.contract.effects for statement in bound):
            self.fail("LANGUAGE_INVALID", "OUTPUT_REQUIRED", "An indicator requires an output call.", span, "rule.grammar")
        return bound

    def validate_functions(self):
        from dataclasses import fields, is_dataclass

        def walk(value):
            yield value
            if is_dataclass(value):
                for field in fields(value):
                    yield from walk(getattr(value, field.name))
            elif isinstance(value, tuple):
                for child in value:
                    yield from walk(child)

        graph = {}

        def check_return(nodes):
            tail = nodes[-1]
            if not isinstance(tail, (parser.Call, parser.ExpressionStatement, parser.If)):
                self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "Function requires a final scalar expression.", tail.span)
            if isinstance(tail, parser.If):
                check_return(tail.body)
                if tail.otherwise:
                    check_return(tail.otherwise)

        for name, definition in self.shared["functions"].items():
            check_return(definition.body)
            resolver = Resolver(self.fail, f"template:{name}/", True)
            local = {p.name: BindingInfo(parser.Declaration(p.name, p.annotation, "parameter", None, p.span),
                f"template:{name}", namespace=f"template:{name}/") for p in definition.parameters}
            resolver.statements(definition.body, [self.shared["captures"][name], local], f"template:{name}", True)
            graph[name] = set()
            for node in walk(definition.body):
                known_variable = isinstance(node, parser.Reference) and any(entry["name"] == node.name and entry["category"] == "variables" for entry in self.catalog.snapshot.references[self.catalog.language_version]["entries"])
                if isinstance(node, parser.Reference) and resolver.references.get(id(node)) is None and node.name not in self.catalog.references and not known_variable:
                    self.fail("LANGUAGE_INVALID", "UNDEFINED_VARIABLE", f"No visible function variable {node.name!r}.", node.span, "rule.scope")
                if isinstance(node, parser.Call):
                    if resolver.call_variables.get(id(node)):
                        self.fail("LANGUAGE_INVALID", "CALL_VARIABLE", "A function parameter/variable cannot be called.", node.span)
                    if node.name in self.shared["functions"]:
                        graph[name].add(node.name)
                    elif node.name not in self.catalog.signatures:
                        self.fail("LANGUAGE_INVALID", "SIGNATURE_UNAVAILABLE", "Unknown function in a function body.", node.span)
                    if any(set(s["effects"]) & {"declaration", "emits_plot"} for s, _ in self.catalog.signatures.get(node.name, ())):
                        self.fail("LANGUAGE_INVALID", "GLOBAL_ONLY_CALL", "Declaration and plot calls cannot occur inside functions.", node.span)
        visiting, visited = set(), set()

        def visit(name):
            if name in visiting:
                self.fail("LANGUAGE_INVALID", "FUNCTION_RECURSION", "Recursive function dependency.", self.shared["functions"][name].span)
            if name in visited:
                return
            visiting.add(name)
            for child in graph[name]:
                visit(child)
            visiting.remove(name)
            visited.add(name)

        for name in graph:
            visit(name)
