"""Bind each source argument once against admitted, scoped machine signatures."""

from __future__ import annotations

from dataclasses import dataclass

from packages.compiler.frontend import parser
from packages.compiler.semantic.specification import Snapshot, SpecError, value_sha
from packages.contracts import CallContract, Diagnostic, OpenPineError, ReferenceContract, SourceSpan, ValueType


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


type BoundExpression = BoundLiteral | BoundUnary | BoundReference | BoundConversion | BoundCall


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
                    if result["kind"] == "scalar":
                        if result["qualifier"]["relation"] != "exact":
                            raise SpecError("IMPLEMENTATION_MISSING", "Scalar result qualifier is not implemented")
                        result_type = ValueType(type_options(result["type"])[0], result["qualifier"]["value"])
                    contract = CallContract(
                        symbol["symbol_id"], overload["overload_id"], assertion_id, value_sha(signature),
                        signature["operation"], tuple(parameter["parameter_id"] for parameter in parameters),
                        tuple(type_options(parameter["type"]) for parameter in parameters),
                        tuple(parameter["qualifier_constraint"]["value"] for parameter in parameters),
                        names.index(result["parameter"]) if result["kind"] == "dependent" else None,
                        tuple(signature["effects"]),
                        result_type,
                    )
                    if any(parameter["qualifier_constraint"]["relation"] != "at_most" for parameter in parameters):
                        raise SpecError("IMPLEMENTATION_MISSING", "Qualifier relation is not implemented")
                    self.contracts[contract.overload_id] = contract
                self.signatures.setdefault(symbol["name"], []).append((signature, contract))


class Binder:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.qualifiers = catalog.rules["rule.types"]["qualifier_order"]

    def fail(self, category: str, code: str, message: str, span: SourceSpan, rule="rule.binding"):
        raise OpenPineError(Diagnostic(category, code, message, span, rule, self.catalog.language_version))

    def expression(self, expression: parser.Expression, top_level=False) -> BoundExpression:
        if isinstance(expression, parser.Literal):
            return BoundLiteral(expression.value, literal_type(expression.value), expression.span)
        if isinstance(expression, parser.Reference):
            contract = self.catalog.references.get(expression.name)
            if contract is None:
                self.fail("PROFILE_UNCOVERED", "VALUE_REFERENCE", f"Reference {expression.name!r} is outside this implementation profile.", expression.span)
            return BoundReference(contract, contract.value_type, expression.span)
        if isinstance(expression, parser.Unary):
            operand = self.expression(expression.operand)
            if operand.value_type.base not in ("int", "float"):
                self.fail("LANGUAGE_INVALID", "UNARY_TYPE", "Unary signs require a numeric value.", expression.span, "rule.types")
            return BoundUnary(expression.operator, operand, operand.value_type, expression.span)
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
                if default["kind"] != "literal":
                    self.fail("SPEC_UNRESOLVED", "DEFAULT_UNAVAILABLE", "No usable literal default.", call.span)
                bound[position] = BoundArgument(parameter["parameter_id"],
                    BoundLiteral(default["value"], literal_type(default["value"]), call.span), "default", None)
            value_type = bound[position].expression.value_type
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
        return BoundCall(contract, arguments, order, result_type, call.span)

    def program(self, statements: tuple[parser.Call, ...], empty_span: SourceSpan) -> tuple[BoundCall, ...]:
        bound = tuple(self.expression(statement, top_level=True) for statement in statements)
        declarations = [statement for statement in bound if "declaration" in statement.contract.effects]
        span = statements[0].span if statements else empty_span
        if len(declarations) != self.catalog.rules["rule.grammar"]["program_declaration_count"]:
            self.fail("LANGUAGE_INVALID", "DECLARATION_COUNT", "Exactly one indicator declaration is required.", span, "rule.grammar")
        if bound[0] is not declarations[0]:
            self.fail("PROFILE_UNCOVERED", "DECLARATION_ORDER", "This slice requires the declaration before output calls.", span, "rule.grammar")
        if not any("emits_plot" in statement.contract.effects for statement in bound):
            self.fail("LANGUAGE_INVALID", "OUTPUT_REQUIRED", "An indicator requires an output call.", span, "rule.grammar")
        return bound
