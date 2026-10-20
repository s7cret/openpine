"""Bind each source argument once against admitted, scoped machine signatures."""

from __future__ import annotations

from dataclasses import dataclass

from packages.compiler.frontend import parser
from packages.compiler.semantic.specification import Snapshot, SpecError, value_sha
from packages.contracts import CallContract, Diagnostic, LanguagePolicy, OpenPineError, ReferenceContract, SourceSpan, ValueType, VariableContract


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


type BoundExpression = BoundLiteral | BoundUnary | BoundReference | BoundConversion | BoundCall | BoundNA | BoundLoad | BoundBinary | BoundConditional | BoundHistory
type BoundStatement = BoundCall | BoundStore | BoundIf


@dataclass
class BindingInfo:
    declaration: parser.Declaration
    scope_id: str
    mutable: bool = False
    value_type: ValueType | None = None

    def contract(self):
        span = self.declaration.span
        return VariableContract(f"binding:{span.start}:{span.end}", self.declaration.name, self.value_type,
                                self.declaration.mode, self.scope_id, span)


class Resolver:
    """Resolve lexical owners first, including writes that strengthen qualifiers."""

    def __init__(self, fail):
        self.fail = fail
        self.references, self.declarations, self.assignments, self.call_variables = {}, {}, {}, {}
        self.bindings = []

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
        elif isinstance(node, parser.Call):
            self.call_variables[id(node)] = any(node.name in scope for scope in scopes)
            for argument in node.arguments:
                self.expression(argument.expression, scopes)

    def statements(self, statements, scopes=None, scope_id="scope:root"):
        scopes = [{}] if scopes is None else scopes
        for node in statements:
            if isinstance(node, parser.Declaration):
                if node.name in scopes[-1]:
                    self.fail("LANGUAGE_INVALID", "DUPLICATE_VARIABLE", f"Variable {node.name!r} already exists in this scope.", node.span, "rule.scope")
                self.expression(node.expression, scopes)
                info = BindingInfo(node, scope_id, node.mode == "var")
                scopes[-1][node.name] = info
                self.declarations[id(node)] = info
                self.bindings.append(info)
            elif isinstance(node, parser.Reassignment):
                info = next((scope[node.name] for scope in reversed(scopes) if node.name in scope), None)
                if info is None:
                    self.fail("LANGUAGE_INVALID", "ASSIGN_UNDEFINED", f"Cannot assign undeclared variable {node.name!r}.", node.span, "rule.scope")
                info.mutable = True
                self.assignments[id(node)] = info
                self.expression(node.expression, scopes)
            elif isinstance(node, parser.If):
                self.expression(node.condition, scopes)
                self.statements(node.body, [*scopes, {}], f"scope:{node.span.start}:then")
                self.statements(node.otherwise, [*scopes, {}], f"scope:{node.span.start}:else")
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
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.qualifiers = catalog.rules["rule.types"]["qualifier_order"]
        self.resolver = Resolver(self.fail)

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
        if isinstance(expression, parser.Literal):
            if expression.value is None:
                return BoundNA(ValueType("na"), expression.span)
            return BoundLiteral(expression.value, literal_type(expression.value), expression.span)
        if isinstance(expression, parser.Reference):
            info = self.resolver.references.get(id(expression))
            if info is not None:
                return BoundLoad(info.contract(), info.value_type, expression.span)
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
                sid = f"expression:{operand.span.start}:{operand.span.end}"
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
        if self.resolver.call_variables.get(id(expression)):
            self.fail("LANGUAGE_INVALID", "CALL_VARIABLE", "A variable cannot be called as a function.", expression.span, "rule.scope")
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
        return BoundCall(contract, arguments, order, result_type, call.span)

    def statements(self, statements, scope_id="scope:root"):
        bound = []
        for node in statements:
            if isinstance(node, parser.Declaration):
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
                body = self.statements(node.body, f"scope:{node.span.start}:then")
                otherwise = self.statements(node.otherwise, f"scope:{node.span.start}:else")
                bound.append(BoundIf(condition, body, otherwise, node.span))
            else:
                if scope_id != "scope:root" and any(signature["effects"] and set(signature["effects"]) & {"declaration", "emits_plot"} for signature, _ in self.catalog.signatures.get(node.name, ())):
                    self.fail("LANGUAGE_INVALID", "GLOBAL_ONLY_CALL", "Declaration and plot calls must be global.", node.span, "rule.scope")
                bound.append(self.expression(node, top_level=scope_id == "scope:root"))
        return tuple(bound)

    @property
    def bindings(self):
        return tuple(info.contract() for info in self.resolver.bindings)

    def program(self, statements: tuple[parser.Statement, ...], empty_span: SourceSpan) -> tuple[BoundStatement, ...]:
        self.resolver.statements(statements)
        bound = self.statements(statements)
        declarations = [statement for statement in bound if isinstance(statement, BoundCall) and "declaration" in statement.contract.effects]
        span = statements[0].span if statements else empty_span
        if len(declarations) != self.catalog.rules["rule.grammar"]["program_declaration_count"]:
            self.fail("LANGUAGE_INVALID", "DECLARATION_COUNT", "Exactly one indicator declaration is required.", span, "rule.grammar")
        if bound[0] is not declarations[0]:
            self.fail("PROFILE_UNCOVERED", "DECLARATION_ORDER", "This slice requires the declaration before output calls.", span, "rule.grammar")
        if not any(isinstance(statement, BoundCall) and "emits_plot" in statement.contract.effects for statement in bound):
            self.fail("LANGUAGE_INVALID", "OUTPUT_REQUIRED", "An indicator requires an output call.", span, "rule.grammar")
        return bound
