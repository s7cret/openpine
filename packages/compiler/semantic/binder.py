"""Bind each source argument once against admitted, scoped machine signatures."""

from __future__ import annotations
from packages.contracts.trading import STRATEGY_DECLARATIONS
from packages.contracts.chart_scale import TYPE as CHART_SCALE_TYPE, present as has_chart_scale
from packages.contracts.fill_alerts import EXIT_OPERATIONS, OPTIONAL_MESSAGES

from dataclasses import dataclass

from packages.compiler.frontend import parser
from packages.compiler.semantic.specification import Snapshot, SpecError, value_sha
from packages.contracts import CallContract, Diagnostic, FunctionContract, LanguagePolicy, OpenPineError, ParameterContract, ReferenceContract, SourceSpan, ValueType, VariableContract,InputDefinition
from packages.contracts.scopes import branch_scope, dependency_qualifier, site_id
from packages.contracts.composite import TupleType, qualified
from packages.contracts.arrays import ARRAY_BASES, array_base, array_element, is_array
from packages.contracts.objects import is_object,is_reference
from packages.contracts.chart_points import BASE as POINT, CONSTRUCTORS as POINT_CALLS, DRAWING_CONSTRUCTORS as POINT_DRAWINGS, POINT_SETTERS
from packages.contracts.polylines import (
    CONSTRUCTOR as POLYLINE_NEW, CONSTRUCTORS as POLYLINE_CONSTRUCTORS, OPERATIONS as POLYLINE_OPERATIONS,
    OVERLAY_CONSTRUCTOR as OVERLAY_POLYLINE, STYLED_CONSTRUCTOR as STYLED_POLYLINE, uses_polylines,
)
from packages.contracts.boxes import OPERATIONS as BOX_OPERATIONS, uses_boxes
from packages.contracts.box_borders import (
    CONSTRUCTORS as STYLED_BOX_CONSTRUCTORS, COORDINATE_CONSTRUCTOR as STYLED_COORDINATE_BOX,
    FIELDS as BOX_BORDER_FIELDS, OPERATIONS as BOX_BORDER_OPERATIONS, POINT_CONSTRUCTOR as STYLED_POINT_BOX,
)
from packages.contracts.tables import (
    CONSTRUCTOR as TABLE_NEW, OPERATIONS as TABLE_OPERATIONS, OVERLAY_CONSTRUCTOR as OVERLAY_TABLE, uses_tables,
    STYLED_CONSTRUCTOR as STYLED_TABLE, STYLED_OVERLAY_CONSTRUCTOR as STYLED_OVERLAY_TABLE,
    STYLE_DEFAULTS as TABLE_STYLE_DEFAULTS, CELL_LAYOUT as TABLE_CELL_LAYOUT,
    CELL_LAYOUT_FIELDS as TABLE_CELL_LAYOUT_FIELDS, CELL_LAYOUT_SETTERS as TABLE_CELL_LAYOUT_SETTERS,
    MERGE as TABLE_MERGE,
)
from packages.contracts.linefills import OPERATIONS as LINEFILL_OPERATIONS, uses_linefills
from packages.contracts.drawing_xloc import POSITIONAL as XLOC_POSITIONAL, TOKENS as XLOC_TOKENS
from packages.contracts.drawing_xloc_mutations import OPERATIONS as XLOC_ACCESS, SETTERS as XLOC_SETTERS
from packages.contracts.drawings import BASES as DRAWING_BASES
from packages.contracts.drawing_objects import ALL_KINDS, COPY_KINDS, MUTABLE_TYPES, is_drawing_array
from packages.contracts.drawing_properties import OPERATIONS as DRAWING_PROPERTIES
from packages.contracts.labels import OPERATIONS as LABEL_OPERATIONS, uses_labels
from packages.contracts import box_text, label_text
from packages.contracts.visual_display import DESTINATION_TOKENS, DESTINATION_TYPE, DISPLAY_TYPES, FIELD as DISPLAY_FIELD
from .visual_display import check_arguments as check_display_arguments, check_version as check_display_version
from packages.contracts.label_styles import (
    CONSTRUCTORS as STYLED_LABEL_CONSTRUCTORS, COORDINATE_CONSTRUCTOR as STYLED_COORDINATE_LABEL,
    FIELDS as LABEL_STYLE_FIELDS, OPERATIONS as LABEL_STYLE_OPERATIONS, POINT_CONSTRUCTOR as STYLED_POINT_LABEL,
    PRICE as LABEL_PRICE,
)
from packages.contracts.label_yloc import (
    CONSTRUCTORS as VERTICAL_LABEL_CONSTRUCTORS, COORDINATE_CONSTRUCTOR as VERTICAL_COORDINATE_LABEL,
    OPERATIONS as LABEL_YLOC_OPERATIONS, POINT_CONSTRUCTOR as VERTICAL_POINT_LABEL,
)
from packages.contracts.lines import ALL_OPERATIONS as LINE_OPERATIONS, uses_lines
from packages.contracts.line_styles import (
    CONSTRUCTORS as STYLED_LINE_CONSTRUCTORS, COORDINATE_CONSTRUCTOR as STYLED_COORDINATE_LINE,
    FIELDS as LINE_STYLE_FIELDS, POINT_CONSTRUCTOR as STYLED_POINT_LINE,
    OPERATIONS as LINE_STYLE_OPERATIONS, OVERLAY_COORDINATE_CONSTRUCTOR as OVERLAY_COORDINATE_LINE,
    OVERLAY_POINT_CONSTRUCTOR as OVERLAY_POINT_LINE,
)
from .objects import BoundObject
from .enums import BoundEnum
from .maps import BoundMap
from .matrices import BoundMatrix
from packages.contracts.matrices import is_matrix,element as matrix_element,base as matrix_base
from packages.contracts.maps import is_map,parts as map_parts,base as map_base
from packages.contracts.enums import is_enum


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


@dataclass(frozen=True)
class BoundInput:
    definition: InputDefinition
    value_type: ValueType
    span: SourceSpan


@dataclass(frozen=True)
class BoundTuple:
    elements: tuple[BoundExpression, ...]
    value_type: TupleType
    span: SourceSpan


@dataclass(frozen=True)
class BoundUnpack:
    bindings: tuple[VariableContract | None, ...]
    expression: BoundExpression
    span: SourceSpan


@dataclass(frozen=True)
class BoundFor:
    loop_id: str
    counter: VariableContract
    initial: BoundExpression
    final: BoundExpression
    step: BoundExpression
    statements: tuple[BoundStatement, ...]
    result: BoundExpression | None
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan


@dataclass(frozen=True)
class BoundWhile:
    loop_id: str
    condition: BoundExpression
    statements: tuple[BoundStatement, ...]
    result: BoundExpression | None
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan


@dataclass(frozen=True)
class BoundLoopControl:
    action: str
    loop_id: str
    span: SourceSpan


@dataclass(frozen=True)
class BoundArray:
    operation: str
    element: str
    arguments: tuple[BoundArgument, ...]
    evaluation_order: tuple[int, ...]
    value_type: ValueType
    span: SourceSpan
    namespace: str = ''


@dataclass(frozen=True)
class BoundForIn:
    loop_id: str
    targets: tuple[VariableContract | None, ...]
    collection: BoundExpression
    statements: tuple[BoundStatement, ...]
    result: BoundExpression | None
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan


type BoundExpression = BoundLiteral | BoundUnary | BoundReference | BoundConversion | BoundCall | BoundNA | BoundLoad | BoundBinary | BoundConditional | BoundHistory | BoundBlock | BoundUserCall | BoundInput | BoundTuple | BoundFor | BoundWhile | BoundArray | BoundForIn | BoundObject | BoundEnum | BoundMap | BoundMatrix
type BoundStatement = BoundCall | BoundStore | BoundIf | BoundEvaluate | BoundUnpack | BoundLoopControl


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

    def __init__(self, fail, namespace="", in_function=False, reference_names=(), namespace_prefixes=(), qualified_names=()):
        self.fail = fail
        self.references, self.declarations, self.assignments, self.call_variables = {}, {}, {}, {}
        self.bindings = []
        self.namespace, self.in_function = namespace, in_function
        self.function_captures = {}
        self.method_calls,self.namespace_references=set(),set()
        self.field_references={}
        self.reference_names,self.namespace_prefixes=set(reference_names),set(namespace_prefixes)
        self.qualified_names=set(qualified_names)
        self.loop_depth = 0

    def loop(self, node, scopes, value=False):
        local = {}
        scope_id = branch_scope(node.span, "loop", self.namespace)
        if isinstance(node, parser.For):
            for expression in (node.initial, node.final, node.step):
                self.expression(expression, scopes)
            info = BindingInfo(node.counter, scope_id, True, ValueType("int", "series"), self.namespace)
            local[node.counter.name] = info
            self.declarations[id(node.counter)] = info
            self.bindings.append(info)
        elif isinstance(node, parser.ForIn):
            self.expression(node.collection, scopes)
            for target in node.targets:
                if target.name == '_':continue
                if target.name in local:self.fail('LANGUAGE_INVALID','FOR_IN_TARGET','Iteration variables must be unique.',target.span)
                info = BindingInfo(target, scope_id, True, None, self.namespace)
                local[target.name] = info
                self.declarations[id(target)] = info
                self.bindings.append(info)
        else:
            self.expression(node.condition, scopes)
        self.loop_depth += 1
        try:
            self.statements(node.body, [*scopes, local], scope_id, value)
        finally:
            self.loop_depth -= 1

    def expression(self, node, scopes):
        if isinstance(node, parser.ValueSwitch):
            self.statements(node.body, [*scopes, {}], branch_scope(node.span, 'value:then', self.namespace), True)
        elif isinstance(node, parser.ValueLoop):
            self.loop(node.loop, scopes, True)
        elif isinstance(node, parser.TupleExpression):
            for element in node.elements:self.expression(element,scopes)
        elif isinstance(node, parser.Reference):
            self.references[id(node)] = next((scope[node.name] for scope in reversed(scopes) if node.name in scope), None)
            if self.references[id(node)] is None and node.name not in self.reference_names and node.name not in self.qualified_names and '.' in node.name:
                root,member=node.name.split('.',1)
                owner=next((scope[root] for scope in reversed(scopes) if root in scope),None)
                if owner is not None:self.references[id(node)]=owner;self.field_references[id(node)]=member
        elif isinstance(node,parser.FieldAccess):
            self.expression(node.receiver,scopes)
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
        elif isinstance(node, parser.MemberCall):
            prefix=node.receiver.name if isinstance(node.receiver,parser.Reference) else None
            lexical=prefix is not None and any(prefix.split('.',1)[0] in s for s in scopes)
            static=node.qualified and (node.name in self.qualified_names or prefix in self.namespace_prefixes or not lexical and prefix not in self.reference_names)
            if static:
                self.namespace_references.add(id(node.receiver))
                self.call_variables[id(node)]=any(node.name in s for s in scopes)
            else:
                self.method_calls.add(id(node));self.expression(node.receiver,scopes)
            for argument in node.arguments:self.expression(argument.expression,scopes)
        elif isinstance(node, parser.Call):
            self.call_variables[id(node)] = any(node.name in scope for scope in scopes)
            for argument in node.arguments:
                self.expression(argument.expression, scopes)

    def statements(self, statements, scopes=None, scope_id="scope:root", value_tail=False):
        scopes = [{}] if scopes is None else scopes
        for node in statements:
            if isinstance(node,(parser.ObjectType,parser.EnumType)):
                if scope_id!='scope:root' or self.in_function:self.fail('LANGUAGE_INVALID','ENUM_GLOBAL_ONLY' if isinstance(node,parser.EnumType) else 'UDT_GLOBAL_ONLY','Types must be declared globally.',node.span)
            elif isinstance(node,parser.FieldReassignment):
                self.expression(node.target,scopes);self.expression(node.expression,scopes)
            elif isinstance(node, parser.Switch):
                tail = value_tail and node is statements[-1]
                self.statements(node.body, [*scopes, {}], branch_scope(node.span, 'value:then' if tail else 'then', self.namespace), tail)
            elif isinstance(node, (parser.For, parser.While, parser.ForIn)):
                self.loop(node, scopes, value_tail and node is statements[-1])
            elif isinstance(node, parser.LoopControl):
                if not self.loop_depth:
                    self.fail("LANGUAGE_INVALID", "LOOP_CONTROL_SCOPE", "Loop control requires an enclosing loop in this function.", node.span)
            elif isinstance(node, parser.Function):
                if scope_id != "scope:root" or self.in_function:
                    self.fail("LANGUAGE_INVALID", "FUNCTION_GLOBAL_ONLY", "Function definitions must be global.", node.span)
                self.function_captures[site_id('function',node.span)] = dict(scopes[0])
            elif isinstance(node, parser.ExpressionStatement):
                self.expression(node.expression, scopes)
            elif isinstance(node, parser.TupleDeclaration):
                self.expression(node.expression,scopes)
                for target in node.targets:
                    if target.name=='_':continue
                    if target.name in scopes[-1]:self.fail('LANGUAGE_INVALID','DUPLICATE_VARIABLE','Tuple targets must be new unique declarations.',target.span)
                    info=BindingInfo(target,scope_id,namespace=self.namespace)
                    scopes[-1][target.name]=info; self.declarations[id(target)]=info; self.bindings.append(info)
            elif isinstance(node, parser.Declaration):
                if node.name in scopes[-1]:
                    self.fail("LANGUAGE_INVALID", "DUPLICATE_VARIABLE", f"Variable {node.name!r} already exists in this scope.", node.span, "rule.scope")
                self.expression(node.expression, scopes)
                info = BindingInfo(node, scope_id, node.mode in ('var','varip'), namespace=self.namespace)
                scopes[-1][node.name] = info
                self.declarations[id(node)] = info
                self.bindings.append(info)
            elif isinstance(node, parser.Reassignment):
                info = next((scope[node.name] for scope in reversed(scopes) if node.name in scope), None)
                if info is None:
                    self.fail("LANGUAGE_INVALID", "ASSIGN_UNDEFINED", f"Cannot assign undeclared variable {node.name!r}.", node.span, "rule.scope")
                if info.declaration.mode == "counter":
                    self.fail("PROFILE_UNCOVERED", "LOOP_COUNTER_WRITE", "Explicit counter reassignment is outside this loop profile.", node.span)
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
        from .environment import install as install_environment
        install_environment(self)
        from .technical import install as install_technical
        install_technical(self)
        from .trend_indicators import install as install_trends
        install_trends(self)
        from .momentum import install as install_momentum
        install_momentum(self)
        from .volume_indicators import install as install_volume
        install_volume(self)
        from .window_statistics import install as install_statistics
        install_statistics(self)
        from .correlation import install as install_correlation
        install_correlation(self)
        from .pivots import install as install_pivots
        install_pivots(self)
        from .pivot_levels import install as install_pivot_levels
        install_pivot_levels(self)
        from .signals import install as install_signals
        install_signals(self)
        from .composite import install as install_composite
        install_composite(self)
        from .scalars import install as install_scalars
        install_scalars(self)
        from .numerics import install as install_numerics
        install_numerics(self)
        from .dynamic_windows import install as install_dynamic_windows
        install_dynamic_windows(self)
        from .window_indicators import install as install_window_indicators
        install_window_indicators(self)
        from .channel_indicators import install as install_channels
        install_channels(self)
        from .dynamic_signals import install as install_dynamic_signals
        install_dynamic_signals(self)
        from .requests import install
        install(self)
        from .strategies import install as install_strategies
        install_strategies(self)
        self.display_calls={}
        self.destination_calls={}
        from .colors import install as install_colors
        install_colors(self)
        from .bands import install as install_bands
        install_bands(self)
        from .markers import install as install_markers
        install_markers(self)
        from .labels import install as install_labels
        install_labels(self)
        from .number_format import install as install_number_format
        install_number_format(self)
        from .chart_scale import install as install_chart_scale
        install_chart_scale(self)
        from .visual_order import install as install_visual_order
        install_visual_order(self)
        from .drawing_limits import install as install_drawing_limits
        install_drawing_limits(self)
        from .drawing_objects import install as install_drawing_objects
        install_drawing_objects(self)
        from .alerts import install as install_alerts
        install_alerts(self)

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
        self.loop_stack = []
        self.shared = shared if shared is not None else {"functions": {}, "groups": {}, "definitions": {}, "captures": {}, "bindings": [], "instances": 0, "nodes": 0, "depth": 0}
        if 'namespace_prefixes' not in self.shared:
            self.shared['namespace_prefixes']={name.rsplit('.',1)[0] for name in (*catalog.signatures,*catalog.references) if '.' in name}|{'array'}
            # Preserve unavailable builtin API diagnostics across versions,
            # without reserving every future namespace as a receiver variable.
            self.shared['qualified_names']={e['name'] for ref in catalog.snapshot.references.values() for e in ref['entries'] if '.' in e['name']}
        self.resolver = Resolver(self.fail, namespace, bool(stack),(*catalog.references,*getattr(catalog,'color_constants',{})),self.shared['namespace_prefixes'],self.shared['qualified_names'])

    def limit(self, span):
        self.shared["nodes"] += 1
        if self.shared["nodes"] > 8192:
            self.fail("PROFILE_UNCOVERED", "FUNCTION_EXPANSION", "Expanded program exceeds 8192 nodes.", span)

    def qualifier(self, *expressions):
        return max((expression.value_type.qualifier for expression in expressions), key=self.qualifiers.index)

    def coerce(self, expression, base, code="ASSIGNMENT_TYPE"):
        if not isinstance(base, str):
            self.fail('LANGUAGE_INVALID', code, 'A scalar argument cannot receive a tuple result.', expression.span, 'rule.types')
        if base.startswith('array<'):base = 'array.' + base[6:-1]
        base=self.type_base(base)
        if expression.value_type.base == base:
            return expression
        target = ValueType(base, expression.value_type.qualifier)
        if base in DRAWING_BASES or is_reference(base):target = ValueType(base, 'series')
        if isinstance(expression, BoundNA) and (is_enum(base) or is_reference(base) or base in ("int", "float", "string", "color", *DRAWING_BASES) or (base == "bool" and self.catalog.language_version < 6)):
            if base == 'string':self.fail('PROFILE_UNCOVERED','ARRAY_STRING_NA','Bare string na is outside this profile; array getters may return a missing string.',expression.span)
            return BoundNA(target, expression.span)
        if expression.value_type.base == "int" and base == "float":
            return BoundConversion(expression, target, expression.span)
        names={site_id('udt',n.span):n.name for n in self.shared.get('object_definitions',{}).values()}
        names.update({'array.'+k:'array<'+v+'>' for k,v in tuple(names.items())})
        self.fail("LANGUAGE_INVALID", code, f"Cannot use {names.get(expression.value_type.base,expression.value_type.base)} as {names.get(base,base)}.", expression.span, "rule.types")

    def condition(self, expression):
        if expression.value_type.base == "bool":
            return expression
        if self.catalog.policy.numeric_conditions and expression.value_type.base in ("int", "float"):
            return BoundConversion(expression, ValueType("bool", expression.value_type.qualifier), expression.span)
        self.fail("LANGUAGE_INVALID", "CONDITION_TYPE", "Condition requires a boolean value.", expression.span, "rule.types")

    def common(self, left, right, span):
        if isinstance(left.value_type,TupleType) or isinstance(right.value_type,TupleType):
            if not isinstance(left.value_type,TupleType) or not isinstance(right.value_type,TupleType) or len(left.value_type.elements)!=len(right.value_type.elements):
                self.fail('LANGUAGE_INVALID','TUPLE_TYPE','Tuple branches must have equal arity.',span)
            elements=[]
            for a,b in zip(left.value_type.elements,right.value_type.elements,strict=True):
                if {a.base,b.base}<={'int','float'}:base='float' if 'float' in (a.base,b.base) else 'int'
                elif a.base==b.base:base=a.base
                else:self.fail('LANGUAGE_INVALID','TUPLE_TYPE','Tuple branch elements have incompatible types.',span)
                elements.append(ValueType(base,max((a.qualifier,b.qualifier),key=self.qualifiers.index)))
            typ=TupleType(tuple(elements))
            return (left if left.value_type==typ else BoundConversion(left,typ,left.span),right if right.value_type==typ else BoundConversion(right,typ,right.span),typ)
        bases = {left.value_type.base, right.value_type.base}
        object_bases={b for b in bases if is_object(b) or is_enum(b) or b=='color'}
        if len(object_bases)==1 and bases<=object_bases|{'na'}:
            base=next(iter(object_bases));return self.coerce(left,base),self.coerce(right,base),base
        if bases <= {"int", "float", "na"}:
            base = "float" if "float" in bases else "int" if "int" in bases else "float"
            return self.coerce(left, base), self.coerce(right, base), base
        if all(base in bases for base in DISPLAY_TYPES):
            return left, right, DESTINATION_TYPE
        if len(bases) == 1 and bases <= {"bool", "string", "sort_order", "color", "plot_style", "plot_line_style", "hline_style", *DISPLAY_TYPES, label_text.TEXT_FORMAT}:
            return left, right, left.value_type.base
        if len(bases) == 1 and all(is_array(b) or is_map(b) or is_matrix(b) for b in bases):
            return left, right, left.value_type.base
        if 'na' in bases and len(bases) == 2 and all(is_array(b) or is_map(b) or is_matrix(b) for b in bases-{'na'}):
            base = next(iter(bases - {'na'}))
            return self.coerce(left, base), self.coerce(right, base), base
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
        if isinstance(expression, parser.ValueSwitch):
            block = self.value_block(expression.body, branch_scope(expression.span, 'value:then', self.namespace), expression.span)
            if is_array(block.value_type.base):
                self.fail('PROFILE_UNCOVERED', 'SWITCH_RESULT_TYPE', 'Reference-valued switch is outside this profile.', expression.span)
            return block
        if isinstance(expression, parser.ValueLoop):
            return self.loop(expression.loop, value=True)
        self.limit(expression.span)
        if isinstance(expression,parser.Call) and expression.name=='timestamp' and self.catalog.language_version==1:
            self.fail('PROFILE_UNCOVERED','ENVIRONMENT_VERSION','timestamp has no admitted v1 signature.',expression.span)
        if isinstance(expression,parser.TupleExpression):
            if not getattr(self.catalog.snapshot,'composite',None):self.fail('PROFILE_UNCOVERED','COMPOSITE_PROFILE','Tuple expressions require the composite profile.',expression.span)
            elements=tuple(self.expression(e) for e in expression.elements)
            from packages.contracts.product_results import element,admit
            if any(e.value_type.base not in ('int','float','bool','string', 'color', 'plot_style') for e in elements):
                from .specification import ROOT
                try:admit(ROOT,self.catalog.snapshot,self.catalog.language_version)
                except ValueError as error:self.fail("SPEC_UNRESOLVED","PRODUCT_RESULT_PROFILE",str(error),expression.span)
            if any(not element(e.value_type.base,self.catalog.language_version) for e in elements):
                self.fail('LANGUAGE_INVALID','TUPLE_TYPE','Tuples require resolved scalar elements; nested tuples and bare na are excluded.',expression.span)
            typ=TupleType(tuple(e.value_type for e in elements))
            return BoundTuple(elements,qualified(typ,self.qualifier(*elements)),expression.span)
        if isinstance(expression,parser.Call) and (expression.name=='input' or expression.name.startswith('input.')):
            self.fail('PROFILE_UNCOVERED','INPUT_SCOPE','Input calls require a direct ordinary global declaration.',expression.span)
        if isinstance(expression, parser.ColorLiteral):
            return BoundLiteral(expression.value,ValueType('color'),expression.span)
        if isinstance(expression, parser.Reference) and self.resolver.references.get(id(expression)) is None and expression.name in getattr(self.catalog,'color_constants',{}):
            value,base=self.catalog.color_constants[expression.name]
            return BoundLiteral(value,ValueType(base),expression.span)
        if isinstance(expression, parser.Literal):
            if expression.value is None:
                return BoundNA(ValueType("na"), expression.span)
            return BoundLiteral(expression.value, literal_type(expression.value), expression.span)
        from .enums import member as enum_member
        enum_value=enum_member(self,expression)
        if enum_value is not None:return enum_value
        if isinstance(expression,parser.FieldAccess) or id(expression) in self.resolver.field_references:
            from .objects import get
            return get(self,expression)
        if isinstance(expression, parser.Reference):
            info = self.resolver.references.get(id(expression))
            if info is not None:
                if info.value_type is None:
                    self.fail("LANGUAGE_INVALID", "FUNCTION_CAPTURE_ORDER", "A function capture must be initialized before the call.", expression.span, "rule.scope")
                if self.namespace and (is_drawing_array(info.value_type.base) or info.value_type.base in MUTABLE_TYPES):
                    self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing arrays are local to the script calculation.', expression.span)
                return BoundLoad(info.contract(), info.value_type, expression.span)
            if expression.name in ALL_KINDS:
                if self.catalog.language_version < 5:
                    self.fail('LANGUAGE_INVALID', 'DRAWING_ALL_VERSION', 'Drawing inventories require Pine v5 or v6.', expression.span)
                if self.namespace:
                    self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing inventories are local to the indicator calculation.', expression.span)
            if expression.name.startswith(DISPLAY_FIELD+'.'):
                if self.catalog.language_version<4:
                    self.fail('LANGUAGE_INVALID','VISUAL_DISPLAY_VERSION','Display modes require Pine v4 or later.',expression.span)
                if expression.name in DESTINATION_TOKENS and self.catalog.language_version < 5:
                    self.fail('LANGUAGE_INVALID','VISUAL_DISPLAY_VERSION','Display destinations require Pine v5 or later.',expression.span)
                self.fail('PROFILE_UNCOVERED','VISUAL_DISPLAY_MODE','This display mode is outside the profile.',expression.span)
            from packages.contracts.environment import ENVIRONMENT_NAMES
            if expression.name in ENVIRONMENT_NAMES and expression.name not in self.catalog.references:
                self.fail('PROFILE_UNCOVERED','ENVIRONMENT_VERSION','Environment reference is unavailable in this version/profile.',expression.span)
            if expression.name in self.catalog.constants:
                value=self.catalog.constants[expression.name]
                return BoundLiteral(value,literal_type(value),expression.span)
            from .technical import reference as technical_reference
            technical = technical_reference(self, expression)
            if technical is not None:return technical
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
            if isinstance(expression.operand,parser.FieldAccess) or id(expression.operand) in self.resolver.field_references:
                code='UDT_FIELD_HISTORY_VERSION' if self.catalog.language_version==6 else 'UDT_FIELD_HISTORY_PROFILE'
                category='LANGUAGE_INVALID' if self.catalog.language_version==6 else 'PROFILE_UNCOVERED'
                self.fail(category,code,'Use a historic object reference or store the field in a scalar variable before applying history.',expression.span)
            if isinstance(expression.operand, parser.History):
                self.fail("LANGUAGE_INVALID", "HISTORY_CHAIN", "Repeated history indexing is not allowed.", expression.span, "rule.history")
            operand, offset = self.expression(expression.operand), self.expression(expression.offset)
            if operand.value_type.base not in ("int", "float", "bool", "color", "sort_order") and not is_reference(operand.value_type.base) and not is_enum(operand.value_type.base) or operand.value_type.qualifier != "series":
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
            left_display = left.value_type.base in DISPLAY_TYPES
            right_display = right.value_type.base in DISPLAY_TYPES
            if left_display or right_display:
                if self.catalog.language_version < 5:
                    self.fail('LANGUAGE_INVALID','VISUAL_DISPLAY_VERSION','Display combinations require Pine v5 or later.',expression.span)
                if expression.operator not in ('+', '-') or not (left_display and right_display):
                    self.fail('PROFILE_UNCOVERED','VISUAL_DISPLAY_MODE','Display modes combine using + or -.',expression.span)
                return BoundBinary(expression.operator, left, right,
                                   ValueType(DESTINATION_TYPE, self.qualifier(left,right)), expression.span)
            if expression.operator in ('==','!=') and (is_object(left.value_type.base) or is_object(right.value_type.base)):
                self.fail('PROFILE_UNCOVERED','UDT_EQUALITY','Direct object equality is outside the flat object profile.',expression.span)
            operator = expression.operator
            if operator in ("and", "or"):
                left, right = self.condition(left), self.condition(right)
                base = "bool"
            else:
                left, right, base = self.common(left, right, expression.span)
                if is_matrix(base):self.fail('PROFILE_UNCOVERED','MATRIX_OPERATOR','Matrix references support na(), assignment and conditional selection.',expression.span)
                if is_map(base):self.fail('PROFILE_UNCOVERED','MAP_OPERATOR','Map references support na(), assignment and conditional selection, not operators.',expression.span)
                if is_array(base):self.fail('PROFILE_UNCOVERED','ARRAY_OPERATOR','Array references support na(), assignment and conditional selection, not operators.',expression.span)
                if isinstance(base,TupleType):self.fail('LANGUAGE_INVALID','TUPLE_OPERATOR','Tuples cannot be arithmetic or comparison operands.',expression.span)
                if base == label_text.TEXT_FORMAT and operator != "+":
                    self.fail("LANGUAGE_INVALID", "TEXT_FORMAT_OPERATOR", "Text formats combine with +.", expression.span)
                if operator in ("==", "!=", "<", "<=", ">", ">="):
                    if operator not in ("==", "!=") and base not in ("int", "float"):
                        self.fail("LANGUAGE_INVALID", "ORDERING_TYPE", "Ordering requires numeric operands.", expression.span, "rule.types")
                    base = "bool"
                elif base not in ("int", "float") and not (operator == "+" and base in ("string", label_text.TEXT_FORMAT)):
                    self.fail("LANGUAGE_INVALID", "ARITHMETIC_TYPE", "Arithmetic requires numeric operands.", expression.span, "rule.types")
                elif operator == "/" and (base == "float" or self.catalog.policy.integer_division == "float"):
                    base = "float"
            return BoundBinary(operator, left, right, ValueType(base, self.qualifier(left, right)), expression.span)
        if isinstance(expression, parser.Conditional):
            condition = self.condition(self.expression(expression.condition))
            left, right, base = self.common(self.expression(expression.when_true), self.expression(expression.when_false), expression.span)
            if isinstance(base,TupleType):self.fail('PROFILE_UNCOVERED','TUPLE_TERNARY','Tuple branches use a value-if block, not the ternary operator.',expression.span)
            return BoundConditional(condition, left, right, ValueType(base, self.qualifier(condition, left, right)), expression.span)
        if isinstance(expression, parser.ValueIf):
            return self.value_if(expression)
        if isinstance(expression,parser.MemberCall) and id(expression) in self.resolver.method_calls:
            from .methods import bind
            return bind(self,expression)
        if self.resolver.call_variables.get(id(expression)):
            self.fail("LANGUAGE_INVALID", "CALL_VARIABLE", "A variable cannot be called as a function.", expression.span, "rule.scope")
        if expression.name in self.shared["functions"]:
            return self.user_call(expression)
        from .objects import named
        object_call=named(self,expression)
        if object_call is not None:return object_call
        if expression.name.startswith('matrix.'):
            from .matrices import bind
            return bind(self,expression)
        if expression.name.startswith('map.'):
            from .maps import bind
            return bind(self,expression)
        if expression.name.startswith('array.'):
            from .arrays import bind
            return bind(self, expression)
        return self.builtin_call(expression, top_level)

    def builtin_call(self, expression: parser.Call, top_level=False, *, receiver=None):
        from packages.contracts.alerts import VERSIONS as ALERT_VERSIONS
        if expression.name in ALERT_VERSIONS and self.catalog.language_version < ALERT_VERSIONS[expression.name]:
            self.fail('LANGUAGE_INVALID', 'ALERT_VERSION', f'{expression.name} is unavailable in this Pine version.', expression.span)
        if expression.name in ('na','str.tostring') and len(expression.arguments)==1:
            argument=self.expression(expression.arguments[0].expression)
            from .enums import call as enum_call
            selected=enum_call(self,expression,(argument,))
            if selected is not None:return selected
            prepared_arguments=(argument,)
        else:prepared_arguments=None
        if expression.name == 'na' and len(expression.arguments) == 1:
            argument = prepared_arguments[0]
            if is_matrix(argument.value_type.base):
                from .matrices import bind
                return bind(self,expression,prepared=(argument,),is_na=True)
            if is_map(argument.value_type.base):
                from .maps import bind
                return bind(self,expression,prepared=(argument,),is_na=True)
            if is_object(argument.value_type.base):
                from .objects import operation
                return operation(self,'is_na',argument.value_type.base,[('id',argument)],expression.span,typ=ValueType('bool','series'))
            if argument.value_type.base=='color':
                from .colors import na_call
                return na_call(self,expression,argument)
            if is_array(argument.value_type.base) or argument.value_type.base == 'string' and self.catalog.language_version >= 4:
                from .arrays import bind_is_na
                return bind_is_na(self, expression, argument)
            # Do not evaluate/bind stateful nested expressions twice.
            prepared_arguments = (argument,)
        if expression.name=='nz':
            from .bands import nz_call
            prepared_arguments=tuple(self.expression(a.expression) for a in expression.arguments)
            color_nz=nz_call(self,expression,prepared_arguments)
            if color_nz is not None:return color_nz
        if self.catalog.language_version < 5 and expression.name in (*POINT_CALLS, *POINT_SETTERS):
            self.fail('LANGUAGE_INVALID', 'POINT_VERSION', 'chart.point requires Pine v5 or v6.', expression.span)
        if expression.name in COPY_KINDS and self.catalog.language_version < 5:
            self.fail('LANGUAGE_INVALID', 'DRAWING_COPY_VERSION', 'Drawing copies require Pine v5 or v6.', expression.span)
        if any(expression.name in operations for operations in (
                COPY_KINDS, DRAWING_PROPERTIES, LABEL_OPERATIONS, LINE_OPERATIONS, BOX_OPERATIONS, TABLE_OPERATIONS, LINEFILL_OPERATIONS,
                POINT_SETTERS, POLYLINE_OPERATIONS, XLOC_ACCESS, BOX_BORDER_OPERATIONS,
                LABEL_STYLE_OPERATIONS, LABEL_YLOC_OPERATIONS, label_text.OPERATIONS, box_text.OPERATIONS)):
            kind = expression.name.split('.', 1)[0]
            self.check_drawing_type(kind, expression.span)
            if expression.name == TABLE_MERGE and self.catalog.language_version < 5:
                self.fail('LANGUAGE_INVALID', 'TABLE_MERGE_VERSION', 'table.merge_cells requires Pine v5 or later.', expression.span)
            if expression.name == 'box.set_xloc' and self.catalog.language_version < 6:
                self.fail('LANGUAGE_INVALID', 'BOX_SET_XLOC_VERSION', 'box.set_xloc requires Pine v6.', expression.span)
            if self.namespace:
                self.fail('PROFILE_UNCOVERED', f'{kind.upper()}_FUNCTION',
                          f'{kind.capitalize()} effects are outside the function profile.', expression.span)
        for kind, profile in (('label', label_text), ('box', box_text)):
            setter = profile.SETTERS.get(expression.name)
            if expression.name == kind + '.new':
                text_parameters = [a.name for a in expression.arguments if a.name in profile.VERSIONS]
            else:
                text_parameters = [setter] if setter else []
            for name in text_parameters:
                since = profile.VERSIONS[name]
                if self.catalog.language_version < since:
                    self.fail('LANGUAGE_INVALID', f'{kind.upper()}_TEXT_VERSION',
                              f'{name} requires Pine v{since} or later.', expression.span)
        if expression.name == 'table.cell':
            table_options = [a.name for a in expression.arguments if a.name in TABLE_CELL_LAYOUT_FIELDS]
        else:
            field = TABLE_CELL_LAYOUT_SETTERS.get(expression.name)
            table_options = [field] if field else []
        for field in table_options:
            since = TABLE_CELL_LAYOUT_FIELDS[field][1]
            if self.catalog.language_version < since:
                self.fail('LANGUAGE_INVALID', 'TABLE_CELL_LAYOUT_VERSION',
                          f'{field} requires Pine v{since} or later.', expression.span)
        candidates = self.catalog.signatures.get(expression.name)
        if expression.name == 'table.cell' and candidates:
            positionals = sum(a.name is None for a in expression.arguments)
            operation = TABLE_CELL_LAYOUT[self.catalog.language_version] if table_options or positionals > 4 else 'table.cell'
            candidates = [(signature, contract) for signature, contract in candidates if contract.operation == operation]
        if expression.name in ('line.new', 'label.new', 'box.new') and candidates:
            operation = expression.name
            point_variant = operation + '_points'
            point_names = {name for name, base in POINT_DRAWINGS[point_variant] if base == POINT}
            explicit_point = any(a.name in point_names for a in expression.arguments)
            if self.catalog.language_version >= 5:
                prepared_arguments = tuple(self.expression(a.expression) for a in expression.arguments)
            if explicit_point or prepared_arguments and any(a.value_type.base == POINT for a in prepared_arguments):
                if self.catalog.language_version < 5:
                    self.fail('LANGUAGE_INVALID', 'POINT_VERSION', 'chart.point requires Pine v5 or v6.', expression.span)
                operation = point_variant
            elif any(a.name == 'xloc' for a in expression.arguments):
                operation += '_xloc'
            elif operation == 'line.new' and any(a.name == 'extend' for a in expression.arguments):
                operation = 'line.new_extended'
            if expression.name == 'line.new':
                with_points = operation == point_variant
                positional = sum(a.name is None for a in expression.arguments)
                overlay_constructor = OVERLAY_POINT_LINE if with_points else OVERLAY_COORDINATE_LINE
                if positional >= len(LINE_STYLE_OPERATIONS[overlay_constructor]) or any(
                        a.name == 'force_overlay' for a in expression.arguments):
                    if self.catalog.language_version < 5:
                        self.fail('LANGUAGE_INVALID', 'LINE_OVERLAY_VERSION', 'force_overlay requires Pine v5 or later.', expression.span)
                    operation = overlay_constructor
                elif positional > (4 if with_points else 6) or any(a.name in LINE_STYLE_FIELDS for a in expression.arguments):
                    operation = STYLED_POINT_LINE if with_points else STYLED_COORDINATE_LINE
            if expression.name == 'box.new':
                with_points = operation == point_variant
                positional = sum(a.name is None for a in expression.arguments)
                border_constructor = STYLED_POINT_BOX if with_points else STYLED_COORDINATE_BOX
                text_constructor = box_text.POINT_CONSTRUCTOR if with_points else box_text.COORDINATE_CONSTRUCTOR
                border_arity = len(BOX_BORDER_OPERATIONS[border_constructor])
                version = self.catalog.language_version
                if positional > border_arity and version < 5:
                    self.fail('LANGUAGE_INVALID', 'BOX_TEXT_VERSION', 'Box text requires Pine v5 or later.', expression.span)
                if positional == len(box_text.OPERATIONS[text_constructor]) and version == 5:
                    self.fail('LANGUAGE_INVALID', 'BOX_TEXT_VERSION', 'text_formatting requires Pine v6 or later.', expression.span)
                if positional > border_arity or any(a.name in box_text.VERSIONS for a in expression.arguments):
                    operation = text_constructor
                elif positional > (3 if with_points else 5) or any(a.name in BOX_BORDER_FIELDS for a in expression.arguments):
                    operation = border_constructor
            if expression.name == 'label.new':
                with_points = operation == point_variant
                positional = sum(a.name is None for a in expression.arguments)
                yloc_position = 3 if with_points else 4
                explicit_yloc = next((i for i, arg in enumerate(expression.arguments)
                                      if arg.name == 'yloc' or i == yloc_position and arg.name is None), None)
                text_constructor = label_text.POINT_CONSTRUCTOR if with_points else label_text.COORDINATE_CONSTRUCTOR
                # force_overlay is followed only by the v6 text_formatting argument.
                force_overlay_arity = len(label_text.OPERATIONS[text_constructor]) - 1
                if self.catalog.language_version < 5 and positional >= force_overlay_arity:
                    self.fail('LANGUAGE_INVALID', 'LABEL_TEXT_VERSION', 'force_overlay requires Pine v5 or later.', expression.span)
                if positional > (8 if with_points else 9) or any(a.name in label_text.VERSIONS for a in expression.arguments):
                    operation = text_constructor
                elif explicit_yloc is not None:
                    if prepared_arguments is None:
                        prepared_arguments = tuple(self.expression(arg.expression) for arg in expression.arguments)
                    location = prepared_arguments[explicit_yloc]
                    if isinstance(location, BoundLiteral) and location.value == LABEL_PRICE:
                        operation = STYLED_POINT_LABEL if with_points else STYLED_COORDINATE_LABEL
                    else:
                        operation = VERTICAL_POINT_LABEL if with_points else VERTICAL_COORDINATE_LABEL
                elif any(a.name in LABEL_STYLE_FIELDS for a in expression.arguments):
                    operation = STYLED_POINT_LABEL if with_points else STYLED_COORDINATE_LABEL
                elif not with_points and positional == 4:
                    operation = 'label.new_xloc'
            candidates = [(signature, contract) for signature, contract in candidates if contract and contract.operation == operation]
        if expression.name in (POLYLINE_NEW, TABLE_NEW) and candidates:
            operation = expression.name
            names = {argument.name for argument in expression.arguments}
            if operation == POLYLINE_NEW and (sum(a.name is None for a in expression.arguments) > len(POLYLINE_OPERATIONS[POLYLINE_NEW])
                    or names & {'line_style', 'line_width'}):
                operation = STYLED_POLYLINE
            elif operation == TABLE_NEW:
                overlay = 'force_overlay' in names
                if overlay and self.catalog.language_version < 5:
                    self.fail('LANGUAGE_INVALID', 'TABLE_OVERLAY_VERSION', 'force_overlay requires Pine v5 or later.', expression.span)
                if names & TABLE_STYLE_DEFAULTS.keys():
                    operation = STYLED_OVERLAY_TABLE if overlay else STYLED_TABLE
                elif overlay:
                    operation = OVERLAY_TABLE
            elif 'force_overlay' in names:
                operation = OVERLAY_POLYLINE
            candidates = [(signature, contract) for signature, contract in candidates if contract and contract.operation == operation]
        from .colors import styled,plot_signature
        from packages.contracts.visual_panes import FLAG, SOURCE_NAMES, WINDOW, WINDOW_SUFFIX
        from packages.contracts.visual_ohlc import KINDS as OHLC_KINDS
        from packages.contracts.markers import WINDOW_KINDS
        check_display_version(self,expression)
        visual_args = {a.name for a in expression.arguments}
        visual_overlay = candidates and expression.name in (*SOURCE_NAMES,*OHLC_KINDS) and FLAG in visual_args
        if visual_overlay and self.catalog.language_version < 5:
            self.fail('LANGUAGE_INVALID', 'VISUAL_OVERLAY_VERSION', 'force_overlay requires Pine v5 or later.', expression.span)
        if candidates and expression.name=='fill':
            from packages.contracts.gradient_fill import FIELDS, OPERATIONS
            gradient = visual_args.intersection(FIELDS) or sum(a.name is None for a in expression.arguments)>=6
            if gradient:
                if self.catalog.language_version<5:
                    self.fail('PROFILE_UNCOVERED','GRADIENT_FILL_VERSION','Gradient fill requires Pine v5 or later.',expression.span)
                candidates = [(sig,c) for sig,c in candidates if c and c.operation in OPERATIONS]
            else:
                windowed = WINDOW in visual_args
                if windowed and self.catalog.language_version<3:
                    self.fail('PROFILE_UNCOVERED','FILL_WINDOW_VERSION','Fill windows require the Pine v3 or later plot overload.',expression.span)
                candidates = [(sig,c) for sig,c in candidates if c and c.operation not in OPERATIONS and c.overload_id.endswith(WINDOW_SUFFIX)==windowed]
        elif styled(expression) and hasattr(self.catalog,'color_plot'):
            if prepared_arguments is None:
                prepared_arguments=tuple(self.expression(a.expression) for a in expression.arguments)
            candidates=[plot_signature(self,expression,prepared_arguments)]
        elif candidates and expression.name in (*WINDOW_KINDS,*OHLC_KINDS) and WINDOW in visual_args:
            if self.catalog.language_version < 3:
                self.fail('LANGUAGE_INVALID', 'SIGNAL_WINDOW_VERSION', 'show_last requires Pine v3 or later.', expression.span)
            candidates = [self.catalog.signal_windows[expression.name]]
        elif visual_overlay and expression.name in SOURCE_NAMES and hasattr(self.catalog, 'color_plot'):
            candidates = [self.catalog.signal_overlays[expression.name]]
        if expression.name=='request.security_lower_tf' and self.catalog.language_version<5:
            self.fail('PROFILE_UNCOVERED','LOWER_TF_VERSION','request.security_lower_tf is attested only in Pine v5/v6.',expression.span)
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
        if prepared_arguments is None:
            prefix = (receiver,) if receiver is not None else ()
            prepared_arguments = (*prefix, *(self.expression(argument.expression)
                                            for argument in expression.arguments[len(prefix):]))
        arguments = prepared_arguments
        if DISPLAY_FIELD in visual_args:
            check_display_arguments(self,expression,arguments)
        matches, errors = [], []
        for signature, contract in implemented:
            try:
                from .dynamic_windows import select as select_window
                signature,contract=select_window(self.catalog,expression,arguments,signature,contract)
                from .dynamic_signals import select as select_signal_window
                signature,contract=select_signal_window(self.catalog,expression,arguments,signature,contract)
                from .channel_indicators import select as select_channel
                signature,contract=select_channel(self.catalog,expression,arguments,signature,contract)
                if contract.operation=='context.lower_tf':
                    from .requests import select_contract
                    contract=select_contract(expression,arguments,contract,self.catalog.language_version,self)
                matches.append(self.call(expression, arguments, signature, contract, top_level))
            except OpenPineError as error:
                errors.append(error)
        if len(matches) > 1:
            self.fail("LANGUAGE_INVALID", "AMBIGUOUS_OVERLOAD", "Multiple overloads match.", expression.span, "rule.overload")
        if not matches:
            if len(errors) == 1:
                raise errors[0]
            self.fail("LANGUAGE_INVALID", "NO_MATCHING_OVERLOAD", "No overload matches the arguments.", expression.span, "rule.overload")
        from .technical import lower as lower_technical
        from .signals import lower as lower_signals
        from .scalars import lower as lower_scalars
        return lower_scalars(self, lower_signals(self, lower_technical(self, matches[0])))

    def call(self, call: parser.Call, expressions: tuple[BoundExpression, ...], signature: dict,
             contract: CallContract, top_level: bool) -> BoundCall:
        for argument, expression in zip(call.arguments, expressions, strict=True):
            if argument.name == DISPLAY_FIELD:
                variants = (self.catalog.destination_calls if expression.value_type.base == DESTINATION_TYPE
                            else self.catalog.display_calls)
                signature, contract = variants.get(contract.overload_id, (signature, contract))
                break
        from .number_format import select as select_number_format, validate as validate_number_format
        from packages.contracts.number_format import fields as number_format_fields
        signature, contract = select_number_format(self, call, signature, contract)
        from .chart_scale import select as select_chart_scale, validate as validate_chart_scale
        signature, contract = select_chart_scale(self, call, signature, contract)
        from .visual_order import select as select_visual_order
        signature, contract = select_visual_order(self, call, signature, contract)
        from .drawing_limits import select as select_drawing_limits, validate as validate_drawing_limit
        from packages.contracts.drawing_limits import fields as drawing_limit_fields
        signature, contract = select_drawing_limits(self, call, signature, contract)
        formatting = number_format_fields(contract.overload_id)
        limits = drawing_limit_fields(contract.overload_id)
        if contract.operation in XLOC_POSITIONAL and sum(a.name is None for a in call.arguments) > XLOC_POSITIONAL[contract.operation]:
            self.fail('PROFILE_UNCOVERED', 'XLOC_POSITIONAL_PROFILE', 'Drawing coordinate mode and styles require named arguments.', call.span)
        if contract.operation == 'box.new' and sum(a.name is None for a in call.arguments) > 4:
            self.fail('PROFILE_UNCOVERED', 'BOX_POSITIONAL_PROFILE', 'Box colors must be supplied by name in this profile.', call.span)
        if contract.operation in POINT_DRAWINGS and sum(a.name is None for a in call.arguments) > 2:
            self.fail('PROFILE_UNCOVERED', 'POINT_POSITIONAL_PROFILE', 'Drawing coordinate mode and styles require named arguments.', call.span)
        if (contract.operation in TABLE_CELL_LAYOUT.values() and len(expressions) > 4
                and call.arguments[4].name is None and expressions[4].value_type.base == 'color'):
            self.fail('PROFILE_UNCOVERED', 'TABLE_POSITIONAL_PROFILE',
                      'Table cell position 5 is width. Supply text_color by name or at position 7.', call.span)
        if (contract.operation in (OVERLAY_TABLE, STYLED_TABLE, STYLED_OVERLAY_TABLE)
                and sum(a.name is None for a in call.arguments) > len(TABLE_OPERATIONS[TABLE_NEW])):
            self.fail('PROFILE_UNCOVERED', 'TABLE_POSITIONAL_PROFILE', 'Table frame, border and overlay options require named arguments.', call.span)
        if contract.operation.startswith('ohlc.'):
            limit = 7 if contract.operation == 'ohlc.plotcandle' and self.catalog.language_version >= 3 else 6
            if sum(a.name is None for a in call.arguments) > limit:
                self.fail('PROFILE_UNCOVERED', 'OHLC_POSITIONAL_PROFILE', 'Extra OHLC presentation parameters must be named.', call.span)
        if contract.operation.startswith('markers.'):
            v=self.catalog.language_version
            limit=(2 if contract.operation=='markers.barcolor' else 3 if v<=4 else 2) if contract.operation in ('markers.bgcolor','markers.barcolor') else 9 if v<=4 else 8
            if contract.operation=='markers.plotarrow':
                limit=8 if v<=4 else 7
            if sum(a.name is None for a in call.arguments)>limit:self.fail('PROFILE_UNCOVERED','SIGNAL_POSITIONAL_PROFILE','Extra signal parameters must be named in this profile.',call.span)
        if contract.operation.startswith('bands.fill_') and sum(a.name is None for a in call.arguments)>3:
            self.fail('PROFILE_UNCOVERED','FILL_ARGUMENTS','Only the first three fill arguments are positional in this profile.',call.span)
        if contract.operation.startswith('bands.gradient_') and sum(a.name is None for a in call.arguments)>6:
            self.fail('PROFILE_UNCOVERED','GRADIENT_FILL_ARGUMENTS','Gradient fill options after the first six arguments must be named.',call.span)
        if contract.operation in ('colors.plot','bands.plot') and self.catalog.language_version>=3 and sum(a.name is None for a in call.arguments)>5:
            self.fail('PROFILE_UNCOVERED','PLOT_POSITIONAL_PROFILE','Only the first five Pine plot parameters are positional in this profile; use named transp before v6.',call.span)
        from packages.contracts.objects import uses_objects
        if contract.operation in ('context.request','context.lower_tf') and uses_objects(expressions):
            self.fail('PROFILE_UNCOVERED','UDT_REQUEST','Object-dependent request graphs are outside this profile.',call.span)
        from packages.contracts.alerts import CONDITION as ALERT_CONDITION
        if contract.operation == ALERT_CONDITION and (not top_level or self.namespace):
            self.fail('LANGUAGE_INVALID', 'ALERTCONDITION_SCOPE', 'alertcondition requires a global statement.', call.span)
        if set(contract.effects) & {"declaration", "emits_plot", "configures_risk"} and not top_level:
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
                elif default["kind"] == "reference":
                    reference = self.catalog.references[default['name']]
                    bound[position] = BoundArgument(parameter['parameter_id'],
                        BoundReference(reference, reference.value_type, call.span), 'default', None)
                    continue
                elif default["kind"] == "na":
                    bound[position]=BoundArgument(parameter['parameter_id'],BoundNA(ValueType('na'),call.span),'default',None)
                    continue
                else:
                    self.fail("SPEC_UNRESOLVED", "DEFAULT_UNAVAILABLE", "No usable literal default.", call.span)
                typed_default = (contract.operation in (*STYLED_LINE_CONSTRUCTORS, *STYLED_LABEL_CONSTRUCTORS,
                                                        *VERTICAL_LABEL_CONSTRUCTORS, *label_text.CONSTRUCTORS,
                                                        'label.set_text_formatting')
                                 or contract.operation.startswith(('colors.', 'bands.', 'markers.', 'ohlc.', 'box.', 'table.', 'polyline.')))
                default_type = ValueType(contract.parameter_types[position][0]) if typed_default else literal_type(value)
                bound[position] = BoundArgument(parameter["parameter_id"],
                    BoundLiteral(value, default_type, call.span), "default", None)
        if contract.result_rule == "common":
            common = bound[0].expression
            for position in range(1, len(parameters)):
                _, _, base = self.common(common, bound[position].expression, call.span)
                common = self.coerce(common, base, 'ARGUMENT_TYPE')
            for position, previous in list(bound.items()):
                bound[position] = BoundArgument(previous.parameter_id,
                    self.coerce(previous.expression, common.value_type.base), previous.origin, previous.source_index)
        if has_chart_scale(contract.overload_id):
            validate_chart_scale(self, call, bound, positions)
        for position, parameter in enumerate(parameters):
            value_type = bound[position].expression.value_type
            if contract.operation in XLOC_SETTERS and parameter['name'] == 'xloc':
                source = call.arguments[bound[position].source_index].expression
                mode = bound[position].expression
                if (not isinstance(source, parser.Reference) or source.name not in XLOC_TOKENS
                        or not isinstance(mode, BoundLiteral) or mode.value != source.name):
                    self.fail('LANGUAGE_INVALID', 'DRAWING_XLOC', 'Expected xloc.bar_index or xloc.bar_time.', mode.span)
            if (contract.operation in (*XLOC_POSITIONAL, *POINT_DRAWINGS, *STYLED_LINE_CONSTRUCTORS,
                                      *STYLED_BOX_CONSTRUCTORS, *STYLED_LABEL_CONSTRUCTORS,
                                      *VERTICAL_LABEL_CONSTRUCTORS, *label_text.CONSTRUCTORS, *box_text.CONSTRUCTORS, *POLYLINE_CONSTRUCTORS)
                    and parameter['name'] == 'xloc'):
                mode = bound[position].expression
                if not isinstance(mode, BoundLiteral) or mode.value not in XLOC_TOKENS:
                    self.fail('LANGUAGE_INVALID', 'DRAWING_XLOC', 'Expected xloc.bar_index or xloc.bar_time.', mode.span)
            if contract.operation in STYLED_LABEL_CONSTRUCTORS and parameter['name'] == 'yloc':
                location = bound[position].expression
                if not isinstance(location, BoundLiteral) or location.value != LABEL_PRICE:
                    self.fail('PROFILE_UNCOVERED', 'LABEL_YLOC', 'Label appearance requires yloc.price.', location.span)
            if contract.operation in POLYLINE_CONSTRUCTORS and parameter['name'] == 'curved':
                curved = bound[position].expression
                if not isinstance(curved, BoundLiteral) or curved.value is not False:
                    self.fail('PROFILE_UNCOVERED', 'POLYLINE_CURVED', 'The straight polyline profile requires curved=false.', curved.span)
            if value_type.base == "na":
                numeric = [base for base in contract.parameter_types[position] if base in ("int", "float")]
                nullable_tooltip = (parameter['name'] == 'tooltip'
                                    and contract.operation in (*TABLE_CELL_LAYOUT.values(), 'table.cell_set_tooltip'))
                nullable_exit_message = (contract.operation in EXIT_OPERATIONS and parameter['name'] in OPTIONAL_MESSAGES)
                if nullable_exit_message or nullable_tooltip or contract.parameter_types[position] in (
                        ('color',), (POINT,), *((base,) for base in DRAWING_BASES)):
                    previous = bound[position]
                    converted = (BoundNA(ValueType('string'), previous.expression.span) if nullable_exit_message
                                 else BoundLiteral('', ValueType('string'), previous.expression.span) if nullable_tooltip
                                 else self.coerce(previous.expression, contract.parameter_types[position][0]))
                    bound[position]=BoundArgument(previous.parameter_id,converted,previous.origin,previous.source_index)
                    value_type=converted.value_type
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
            if contract.operation == ALERT_CONDITION and position > 0:
                from .alerts import constant_text
                from packages.contracts.alerts import valid_message
                argument = bound[position]
                text = constant_text(self, argument.expression)
                if not valid_message(text):
                    self.fail('PROFILE_UNCOVERED', 'ALERT_TEXT', 'Alert title or template exceeds its byte limit.', call.span)
                bound[position] = BoundArgument(argument.parameter_id, BoundLiteral(text, ValueType('string'), argument.expression.span),
                                                argument.origin, argument.source_index)
            if contract.operation == 'alerts.call' and position == 1:
                from packages.contracts.alerts import FREQUENCIES
                frequency = bound[position].expression
                if isinstance(frequency, BoundLiteral) and frequency.value not in FREQUENCIES:
                    self.fail('LANGUAGE_INVALID', 'ALERT_FREQUENCY', 'Unknown alert frequency.', call.span)
            if parameter['name'] in formatting:
                validate_number_format(self, parameter['name'], bound[position], contract)
            if parameter['name'] in limits:
                validate_drawing_limit(self, parameter['name'], bound[position].expression)
        arguments = tuple(bound[position] for position in range(len(parameters)))
        order = tuple(sorted(range(len(arguments)), key=lambda position: (
            arguments[position].origin == "default",
            arguments[position].source_index if arguments[position].source_index is not None else position,
        )))
        result_type = arguments[contract.result_parameter].expression.value_type if contract.result_parameter is not None else contract.result_type
        if contract.result_rule in ("derived", "common"):
            base = arguments[0].expression.value_type.base if contract.result_rule == "common" else contract.result_type.base
            result_type = ValueType(base, self.qualifier(*(a.expression for a in arguments)))
        elif contract.result_rule in ("scalar_simple", "scalar_const_simple"):
            q=self.qualifier(*(a.expression for a in arguments))
            if q=="input" or q=="const" and contract.result_rule=="scalar_simple":q="simple"
            result_type=ValueType(contract.result_type.base,q)
        elif contract.result_rule == "parameter_series":
            result_type = qualified(arguments[contract.result_parameter].expression.value_type, "series")
        elif contract.result_rule in ("fixed_series", "source_series"):
            result_type = qualified(arguments[0].expression.value_type if contract.result_rule == 'source_series' else contract.result_type,'series')
        elif contract.result_rule == 'request_series':
            result_type=qualified(arguments[2].expression.value_type,'series')
        elif contract.result_rule == 'lower_tf_arrays':
            from packages.contracts.lower_tf import result_type as lower_result
            try:result_type=lower_result(arguments[2].expression.value_type)
            except ValueError as error:self.fail('PROFILE_UNCOVERED','LOWER_TF_RESULT',str(error),call.span)
        return BoundCall(contract, arguments, order, result_type, call.span, self.namespace)

    def value_block(self, nodes, scope_id, span):
        if not nodes:
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "A value block requires a final expression.", span)
        prefix = self.statements(nodes[:-1], scope_id)
        tail = nodes[-1]
        if isinstance(tail, parser.Switch):
            result = self.expression(parser.ValueSwitch(tail.body, tail.span))
        elif isinstance(tail, (parser.For, parser.While, parser.ForIn)):
            result = self.loop(tail, value=True)
        elif isinstance(tail, parser.If):
            result = self.expression(parser.ValueIf(tail.condition, tail.body, tail.otherwise, tail.span))
        elif isinstance(tail, parser.ExpressionStatement):
            result = self.expression(tail.expression)
        elif isinstance(tail, parser.Call):
            result = self.expression(tail)
        else:
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "The final statement must return a scalar expression.", tail.span)
        if result.value_type.base == "void":
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "A value block cannot return void.", tail.span)
        if is_drawing_array(result.value_type.base):
            self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing inventories require a direct ordinary binding.', tail.span)
        if result.value_type.base in DRAWING_BASES:
            kind = result.value_type.base
            self.fail('PROFILE_UNCOVERED', f'{kind.upper()}_BLOCK_RESULT',
                      f'{kind.capitalize()} references cannot be returned from value blocks.', tail.span)
        return BoundBlock(prefix, result, scope_id, qualified(result.value_type, dependency_qualifier(prefix, result)), span)

    def loop(self, node, *, value=False):
        scope_id = branch_scope(node.span, "loop", self.namespace)
        loop_id = site_id("loop", node.span, self.namespace)
        if isinstance(node, parser.For):
            initial, final, step = (self.coerce(self.expression(e), "int", "LOOP_BOUND_TYPE")
                for e in (node.initial, node.final, node.step))
            if isinstance(step, BoundLiteral) and step.value <= 0:
                self.fail("LANGUAGE_INVALID", "LOOP_STEP", "Loop step must be a positive integer.", node.step.span)
            counter = self.resolver.declarations[id(node.counter)].contract()
        elif isinstance(node, parser.ForIn):
            collection = self.expression(node.collection)
            if not is_array(collection.value_type.base) and not is_map(collection.value_type.base) and not is_matrix(collection.value_type.base):
                self.fail('LANGUAGE_INVALID','FOR_IN_TYPE','Array iteration requires an array.',node.collection.span)
            element = array_element(collection.value_type.base)
            if is_matrix(collection.value_type.base):element='array.'+matrix_element(collection.value_type.base)
            pair=map_parts(collection.value_type.base)
            if pair is not None and len(node.targets)!=2:self.fail('LANGUAGE_INVALID','MAP_ITERATOR','Map iteration requires key and value targets.',node.span)
            targets = []
            for index, target in enumerate(node.targets):
                if target.name == '_':targets.append(None);continue
                info = self.resolver.declarations[id(target)]
                info.value_type = ValueType(pair[index] if pair is not None else 'int' if len(node.targets) == 2 and index == 0 else element, 'series')
                targets.append(info.contract())
        else:
            condition = self.condition(self.expression(node.condition))
        self.loop_stack.append(loop_id)
        try:
            result = None
            if value:
                tail = node.body[-1]
                if isinstance(tail, (parser.Declaration, parser.Reassignment)):
                    statements = self.statements(node.body, scope_id)
                    store = statements[-1]
                    result = BoundLoad(store.binding, store.binding.value_type, tail.span)
                else:
                    block = self.value_block(node.body, scope_id, node.span)
                    statements, result = block.statements, block.result
                typ = result.value_type
                elements = typ.elements if isinstance(typ, TupleType) else (typ,)
                from packages.contracts.product_results import element,admit
                if any(t.base not in ("int","float","bool") for t in elements):
                    from .specification import ROOT
                    try:admit(ROOT,self.catalog.snapshot,self.catalog.language_version)
                    except ValueError as error:self.fail("SPEC_UNRESOLVED","PRODUCT_RESULT_PROFILE",str(error),node.span)
                if any(not element(t.base,self.catalog.language_version) for t in elements):
                    self.fail("PROFILE_UNCOVERED", "LOOP_RESULT_TYPE", "Loop result type is outside the versioned profile.", node.span)
                typ = qualified(typ, "series")
            else:
                statements = self.statements(node.body, scope_id)
                typ = ValueType("void")
        finally:
            self.loop_stack.pop()
        if isinstance(node, parser.For):
            return BoundFor(loop_id, counter, initial, final, step, statements, result, scope_id, typ, node.span)
        if isinstance(node, parser.ForIn):
            return BoundForIn(loop_id, tuple(targets), collection, statements, result, scope_id, typ, node.span)
        return BoundWhile(loop_id, condition, statements, result, scope_id, typ, node.span)

    def value_if(self, node):
        condition = self.condition(self.expression(node.condition))
        left = self.value_block(node.body, branch_scope(node.span, "value:then", self.namespace), node.span)
        if node.otherwise:
            right = self.value_block(node.otherwise, branch_scope(node.span, "value:else", self.namespace), node.span)
        else:
            base = left.value_type.base
            if base=='tuple':self.fail('PROFILE_UNCOVERED','TUPLE_BRANCH_REQUIRED','Tuple-valued if requires an explicit else.',node.span)
            if base == "string":
                self.fail("PROFILE_UNCOVERED", "STRING_BRANCH_REQUIRED", "String-valued if requires else.", node.span)
            missing = BoundLiteral(False, ValueType("bool"), node.span) if base == "bool" and self.catalog.language_version == 6 else BoundNA(ValueType(base, 'series' if is_reference(base) else 'const'), node.span)
            right = BoundBlock((), missing, branch_scope(node.span, "value:else", self.namespace), missing.value_type, node.span)
        a, b, base = self.common(left.result, right.result, node.span)
        left = BoundBlock(left.statements, a, left.scope_id, qualified(a.value_type, dependency_qualifier(left.statements, a)), left.span)
        right = BoundBlock(right.statements, b, right.scope_id, qualified(b.value_type, dependency_qualifier(right.statements, b)), right.span)
        typ=base if isinstance(base,TupleType) else ValueType(base)
        return BoundConditional(condition, left, right, qualified(typ,self.qualifier(condition, left, right)), node.span)

    def user_call(self, call, *, definition=None, prepared=None):
        from packages.contracts.overloads import choose,bind_positions
        group=self.visible_functions(call.name)
        if definition is None and len(self.shared['groups'][call.name])>1:
            # Shape checks precede expression binding; candidates never execute
            # or specialize bodies. Explicit arguments are bound exactly once.
            names=tuple(a.name for a in call.arguments)
            shaped=[node for node in group if bind_positions(self.function_header(node),names)[1] is None]
            if not shaped:
                errors=[bind_positions(self.function_header(node),names)[1] for node in group]
                code=errors[0] if len(set(errors))==1 else 'FUNCTION_OVERLOAD_NO_MATCH'
                self.fail('LANGUAGE_INVALID',code,'No overload accepts this argument shape.',call.span)
            prepared=tuple(self.expression(a.expression) for a in call.arguments)
            try:header,_=choose(tuple(self.function_header(n) for n in shaped),names,tuple(e.value_type for e in prepared),self.catalog.language_version)
            except ValueError as error:self.fail('LANGUAGE_INVALID',str(error),'No unique overload matches the qualified argument types.',call.span)
            definition=self.shared['definitions'][header.function_id]
        elif definition is None:definition=group[0]
        function_id=site_id('function',definition.span)
        if function_id in self.stack:
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
            supplied[position] = (prepared[i] if prepared is not None else self.expression(argument.expression), i)
        for position, parameter in enumerate(parameters):
            if position not in supplied:
                if parameter.default is None:
                    self.fail('LANGUAGE_INVALID', 'MISSING_ARGUMENT', 'A required function parameter is missing.', call.span)
                supplied[position] = (self.parameter_default(parameter), None)
        call_id = site_id("user", call.span, self.namespace)
        namespace = call_id + "/"
        scope_id = f"scope:{namespace}function"
        child = Binder(self.catalog, self.shared, namespace, (*self.stack, function_id))
        globals_ = self.shared["captures"][function_id]
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
            if expression.value_type.base not in ("int", "float", "bool", "string", 'color', 'plot_style', "sort_order") and not is_reference(expression.value_type.base) and not is_enum(expression.value_type.base):
                self.fail("LANGUAGE_INVALID", "FUNCTION_ARGUMENT_TYPE", "Function parameters require resolved scalar types.", call.span)
            if expression.value_type.base in MUTABLE_TYPES:
                self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing arrays cannot be function arguments.', call.span)
            info = local[parameter.name]
            qualifier = "series" if info.mutable else parameter.qualifier or expression.value_type.qualifier
            if parameter.qualifier == "simple" and (qualifier == "series" or self.qualifiers.index(expression.value_type.qualifier) > self.qualifiers.index("simple")):
                self.fail("LANGUAGE_INVALID", "FUNCTION_ARGUMENT_QUALIFIER", "Simple function parameter cannot receive series values/history.", call.span)
            info.value_type = ValueType(expression.value_type.base, qualifier)
            contract = info.contract()
            contracts.append(contract)
            arguments.append(BoundArgument(contract.binding_id, expression, 'default' if index is None else 'source', index))
        block = child.value_block(definition.body, scope_id, definition.span)
        if block.value_type.base == "na":
            self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "Function return type cannot be inferred from bare na.", definition.span)
        self.shared["bindings"].extend(child.resolver.bindings)
        result_type = qualified(block.value_type, dependency_qualifier(block.statements, block.result, tuple(arguments), tuple(contracts)))
        if result_type.base in ('plot_id','hline_id','hline_style'):self.fail('PROFILE_UNCOVERED','VISUAL_REFERENCE_SCOPE','Visual references cannot be function results.',call.span)
        return BoundUserCall(site_id("function", definition.span), call_id, tuple(contracts), tuple(arguments),
            tuple(sorted(range(len(arguments)), key=lambda i: (arguments[i].source_index is None,
                arguments[i].source_index if arguments[i].source_index is not None else i))), block.statements, block.result, result_type, call.span)

    def function_header(self,node):
        from packages.contracts.overloads import Header,base
        return Header(site_id('function',node.span),tuple(p.name for p in node.parameters),
            tuple(self.type_base(base(p.annotation)) for p in node.parameters),tuple(p.qualifier for p in node.parameters),
            tuple(p.default is None for p in node.parameters))

    def function_key(self,node):
        from packages.contracts.methods import key
        return key(node.name) if isinstance(node,parser.Method) else node.name

    def check_drawing_type(self, kind, span):
        first_version = 5 if kind in ('linefill', 'polyline') else 4
        if self.catalog.language_version < first_version:
            self.fail('LANGUAGE_INVALID', f'{kind.upper()}_VERSION',
                      f'{kind.capitalize()}s require Pine v{first_version} or later.', span)

    def type_base(self,name):
        if name and name.startswith("matrix<"):return matrix_base(self.type_base(name[7:-1]))
        pair=map_parts(name)
        if pair is not None:return map_base(*(self.type_base(t) for t in pair))
        from .objects import type_base
        return type_base(self,name)

    @property
    def object_types(self):
        from .objects import contracts
        return contracts(self)

    @property
    def enum_types(self):return tuple(self.shared.get('enum_definitions',{}).values())

    def visible_functions(self,name,current=None):
        group=self.shared['groups'][name]
        current=current or (self.stack[-1] if self.stack else None)
        if current in self.shared['definitions'] and self.function_key(self.shared['definitions'][current])==name and len(group)>1:
            index=next(i for i,n in enumerate(group) if site_id('function',n.span)==current)
            return group[:index+1]
        return group

    def parameter_default(self, parameter):
        node = parameter.default
        literal = isinstance(node, parser.Literal)
        signed = (isinstance(node, parser.Unary) and node.operator in ('+', '-')
            and isinstance(node.operand, parser.Literal) and type(node.operand.value) in (int, float))
        from .enums import member
        enum_default=member(self,node)
        if not (literal or signed or enum_default is not None):
            self.fail('PROFILE_UNCOVERED', 'FUNCTION_DEFAULT_EXPRESSION', 'This profile admits scalar literals, na and signed numeric literals as defaults.', node.span)
        if enum_default is not None:value=enum_default
        elif literal:
            value = BoundNA(ValueType('na'), node.span) if node.value is None else BoundLiteral(node.value, ValueType('color') if isinstance(node,parser.ColorLiteral) else literal_type(node.value), node.span)
        else:
            raw = node.operand.value if node.operator == '+' else -node.operand.value
            value = BoundLiteral(raw, literal_type(raw), node.span)
        if parameter.annotation:
            value = self.coerce(value, parameter.annotation, 'FUNCTION_ARGUMENT_TYPE')
        elif value.value_type.base == 'na':
            self.fail('LANGUAGE_INVALID', 'FUNCTION_DEFAULT_TYPE', 'An na default requires an explicit type.', node.span)
        if value.value_type.base not in ('int', 'float', 'bool', 'string', 'color', 'plot_style', 'sort_order') and not is_object(value.value_type.base) and not is_enum(value.value_type.base):
            self.fail('PROFILE_UNCOVERED', 'FUNCTION_DEFAULT_TYPE', 'Only scalar defaults are admitted in this profile.', node.span)
        return value

    def statements(self, statements, scope_id="scope:root"):
        bound = []
        for node in statements:
            self.limit(node.span)
            if isinstance(node,(parser.ObjectType,parser.EnumType)):continue
            elif isinstance(node,parser.FieldReassignment):
                from .objects import assign
                bound.append(BoundEvaluate(assign(self,node),node.span))
            elif isinstance(node, parser.Switch):
                bound.append(BoundIf(BoundLiteral(True, ValueType('bool'), node.span),
                    self.statements(node.body, branch_scope(node.span, 'then', self.namespace)), (), node.span))
            elif isinstance(node, (parser.For, parser.While, parser.ForIn)):
                bound.append(BoundEvaluate(self.loop(node), node.span))
            elif isinstance(node, parser.LoopControl):
                if not self.loop_stack:
                    self.fail("LANGUAGE_INVALID", "LOOP_CONTROL_SCOPE", "Loop control requires an enclosing loop.", node.span)
                bound.append(BoundLoopControl(node.action, self.loop_stack[-1], node.span))
            elif isinstance(node, parser.Function):
                continue
            elif isinstance(node, parser.ExpressionStatement):
                bound.append(BoundEvaluate(self.expression(node.expression), node.span))
            elif isinstance(node, parser.TupleDeclaration):
                value=self.expression(node.expression)
                if not isinstance(value.value_type,TupleType) or len(value.value_type.elements)!=len(node.targets):
                    self.fail('LANGUAGE_INVALID','TUPLE_ARITY','Unpacking requires one tuple result with matching arity.',node.span)
                bindings=[]
                for target,typ in zip(node.targets,value.value_type.elements,strict=True):
                    if target.name=='_':bindings.append(None);continue
                    info=self.resolver.declarations[id(target)]
                    info.value_type=ValueType(typ.base,'series' if info.mutable else value.value_type.qualifier)
                    bindings.append(info.contract())
                bound.append(BoundUnpack(tuple(bindings),value,node.span))
            elif isinstance(node, parser.Declaration):
                info = self.resolver.declarations[id(node)]
                from .inputs import is_input,bind as bind_input
                if isinstance(node.expression,parser.Call) and is_input(node.expression.name):
                    if self.resolver.call_variables.get(id(node.expression)) or node.expression.name in self.shared['functions']:
                        self.fail('LANGUAGE_INVALID','INPUT_SHADOW','Input declaration cannot call a shadowed builtin.',node.span)
                    if scope_id!='scope:root' or self.namespace:self.fail('PROFILE_UNCOVERED','INPUT_SCOPE','Inputs must be global.',node.span)
                    value=bind_input(self,node)
                elif isinstance(node.expression,parser.Call) and node.expression.name in ('plot','hline') and hasattr(self.catalog,'band_calls'):
                    if scope_id!='scope:root' or self.namespace or node.mode!='bar':self.fail('PROFILE_UNCOVERED','VISUAL_REFERENCE_SCOPE','Visual references require direct global bar declarations.',node.span)
                    from .bands import handle_call
                    value=handle_call(self,node.expression)
                else:value = self.expression(node.expression)
                if value.value_type.base in DISPLAY_TYPES:
                    self.fail('PROFILE_UNCOVERED','VISUAL_DISPLAY_MODE','Use a display mode directly in the display argument.',node.span)
                if value.value_type.base == CHART_SCALE_TYPE:
                    self.fail('PROFILE_UNCOVERED','CHART_SCALE_VALUE','Use a scale constant directly in the declaration.',node.span)
                if value.value_type.base in ('plot_id','hline_id','hline_style') and (scope_id!='scope:root' or self.namespace or node.mode!='bar' or info.mutable or value.value_type.base=='hline_style' and value.value_type.qualifier not in ('const','input')):
                    self.fail('PROFILE_UNCOVERED','VISUAL_REFERENCE_SCOPE','Visual references/styles are immutable global bindings.',node.span)
                if value.value_type.base in ("void",'tuple'):
                    self.fail("LANGUAGE_INVALID", "VOID_ASSIGNMENT", "A void expression cannot initialize a variable.", node.span, "rule.types")
                if node.annotation:
                    target = self.type_base(node.annotation)
                    if is_drawing_array(target) and not is_drawing_array(value.value_type.base):
                        target = array_base(array_element(target))
                    value = self.coerce(value, target)
                elif value.value_type.base == "na":
                    self.fail("LANGUAGE_INVALID", "UNTYPED_NA", "A na initializer requires an explicit numeric type.", node.span, "rule.types")
                if value.value_type.base in DRAWING_BASES and (node.mode == 'varip' or self.namespace):
                    kind = value.value_type.base
                    self.fail('PROFILE_UNCOVERED', f'{kind.upper()}_BINDING',
                              f'{kind.capitalize()}s require an ordinary or var binding outside functions.', node.span)
                if is_drawing_array(value.value_type.base) and (node.mode != 'bar' or self.namespace):
                    self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing inventories require ordinary bindings outside functions.', node.span)
                if value.value_type.base in MUTABLE_TYPES and (node.mode == 'varip' or self.namespace):
                    self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing arrays require ordinary or var bindings outside functions.', node.span)
                from packages.contracts.object_graphs import collection_varip
                if node.mode=='varip' and not collection_varip(value.value_type.base,{t.type_id:t for t in self.object_types}):
                    self.fail('PROFILE_UNCOVERED','VARIP_GRAPH_TYPE','A varip collection requires UDT fields containing values or value-only collections.',node.span)
                info.value_type = ValueType(value.value_type.base, "series" if info.mutable else value.value_type.qualifier)
                if isinstance(value, BoundInput):
                    if value.definition.input_type == 'bool':
                        self.shared.setdefault('display_input_bindings', set()).add(info.contract().binding_id)
                    elif value.definition.input_type in ('int', 'string'):
                        self.shared.setdefault('format_input_bindings', set()).add(info.contract().binding_id)
                if info.value_type.qualifier=='const':self.shared.setdefault('constant_bindings',{})[info.contract().binding_id]=value
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
                if id(node) not in self.resolver.method_calls and scope_id != "scope:root" and any(signature["effects"] and set(signature["effects"]) & {"declaration", "emits_plot", "configures_risk"} for signature, _ in self.catalog.signatures.get(node.name, ())):
                    self.fail("LANGUAGE_INVALID", "GLOBAL_ONLY_CALL", "Declaration and plot calls must be global.", node.span, "rule.scope")
                expression = self.expression(node, top_level=scope_id == "scope:root")
                bound.append(BoundEvaluate(expression, node.span) if isinstance(expression, (BoundUserCall, BoundArray,BoundObject,BoundMap,BoundMatrix)) else expression)
        return tuple(bound)

    @property
    def bindings(self):
        return tuple(info.contract() for info in (*self.resolver.bindings, *self.shared["bindings"]))

    @property
    def functions(self):
        from packages.contracts import OverloadedFunctionContract,MethodContract
        from packages.contracts.overloads import base
        result=[]
        for function_id,node in self.shared['definitions'].items():
            args=(function_id,node.name,tuple(ParameterContract(p.name,self.type_base(base(p.annotation)),p.qualifier,p.span) for p in node.parameters),
                tuple(info.contract().binding_id for info in self.shared['captures'][function_id].values()),node.span)
            cls=MethodContract if isinstance(node,parser.Method) else OverloadedFunctionContract if len(self.shared['groups'][self.function_key(node)])>1 else FunctionContract
            result.append(cls(*args,tuple(p.default is None for p in node.parameters)) if cls is not FunctionContract else cls(*args))
        return tuple(result)

    def program(self, statements: tuple[parser.Statement, ...], empty_span: SourceSpan) -> tuple[BoundStatement, ...]:
        from dataclasses import fields, is_dataclass
        def review(value):
            if isinstance(value,parser.Call) and value.name.startswith('matrix.') or isinstance(value,(parser.Declaration,parser.Parameter)) and value.annotation and value.annotation.startswith('matrix<'):
                if self.catalog.language_version<5:self.fail('LANGUAGE_INVALID','MATRIX_VERSION','Matrices require Pine v5/v6.',value.span)
                from packages.contracts.matrices import admit
                from .specification import ROOT
                try:admit(ROOT,self.catalog.snapshot,self.catalog.language_version)
                except ValueError as error:self.fail('SPEC_UNRESOLVED','MATRIX_PROFILE',str(error),value.span)
            if isinstance(value,parser.Call) and value.name.startswith('map.') or isinstance(value,(parser.Declaration,parser.Parameter)) and value.annotation and value.annotation.startswith('map<'):
                from packages.contracts.maps import admit
                from .specification import ROOT
                try:admit(ROOT,self.catalog.snapshot,self.catalog.language_version)
                except ValueError as error:self.fail('SPEC_UNRESOLVED','MAP_PROFILE',str(error),value.span)
            if isinstance(value, (parser.Switch, parser.ValueSwitch)) or isinstance(value, parser.Parameter) and value.default is not None:
                from packages.contracts.semantic_core import admit
                from .specification import ROOT
                try:admit(ROOT, self.catalog.snapshot, self.catalog.language_version)
                except ValueError as error:self.fail('SPEC_UNRESOLVED', 'SEMANTIC_CORE_PROFILE', str(error), value.span)
            if self.catalog.language_version<5 and (isinstance(value,parser.Call) and value.name.startswith('map.') or isinstance(value,(parser.Declaration,parser.Parameter)) and value.annotation and value.annotation.startswith('map<')):
                self.fail('LANGUAGE_INVALID','MAP_VERSION','Maps require Pine v5/v6.',value.span)
            if isinstance(value, parser.ForIn) and self.catalog.language_version < 5:
                self.fail('LANGUAGE_INVALID','FOR_IN_VERSION','Array iteration requires Pine v5 or v6.',value.span)
            if isinstance(value, parser.Call) and value.name.startswith('array.') and self.catalog.language_version < 4:
                self.fail('LANGUAGE_INVALID','ARRAY_VERSION','Arrays require Pine v4 or later.',value.span)
            if isinstance(value, parser.Call) and value.name.startswith('array.new<') and self.catalog.language_version < 5:
                self.fail('LANGUAGE_INVALID','ARRAY_SYNTAX_VERSION','Generic constructors require Pine v5 or v6.',value.span)
            if isinstance(value, parser.Call) and value.name.startswith('array.'):
                from packages.contracts.arrays import ELEMENTS, OPERATIONS
                from packages.contracts.drawings import JOURNALED_BASES
                ELEMENTS=(*ELEMENTS,'color',*JOURNALED_BASES)
                name = value.name.removeprefix('array.')
                valid = name in OPERATIONS and name not in ('new','is_na') or name.startswith('new_') and name[4:] in ELEMENTS or name.startswith('new<') and (name[4:-1] in (*ELEMENTS, POINT) or any(isinstance(n,(parser.ObjectType,parser.EnumType)) and n.name==name[4:-1] for n in statements)) and name.endswith('>')
                if not valid:self.fail('PROFILE_UNCOVERED','ARRAY_OPERATION','Array operation/element is outside this profile.',value.span)
            if isinstance(value, (parser.Declaration, parser.Parameter)) and value.annotation in ARRAY_BASES and self.catalog.language_version < 4:
                self.fail('LANGUAGE_INVALID','ARRAY_VERSION','Array types require Pine v4 or later.',value.span)
            if isinstance(value, (parser.Declaration, parser.Parameter)) and is_drawing_array(self.type_base(value.annotation)):
                target = self.type_base(value.annotation)
                kind = array_element(target)
                if array_base(kind) in MUTABLE_TYPES:
                    if self.catalog.language_version < 4:
                        self.fail('LANGUAGE_INVALID', 'ARRAY_VERSION', 'Array types require Pine v4 or later.', value.span)
                    self.check_drawing_type(kind, value.span)
                elif self.catalog.language_version < 5:
                    self.fail('LANGUAGE_INVALID', 'DRAWING_ALL_VERSION', 'Drawing inventories require Pine v5 or v6.', value.span)
                if isinstance(value, parser.Parameter):
                    self.fail('PROFILE_UNCOVERED', 'DRAWING_ARRAY_SCOPE', 'Drawing inventories cannot be function parameters.', value.span)
            if isinstance(value, (parser.Declaration, parser.Parameter)) and value.annotation in DRAWING_BASES:
                kind = value.annotation
                self.check_drawing_type(kind, value.span)
                if isinstance(value, parser.Parameter):
                    self.fail('PROFILE_UNCOVERED', f'{kind.upper()}_FUNCTION',
                              f'{kind.capitalize()} parameters are outside this profile.', value.span)
            if isinstance(value,(parser.Declaration,parser.Parameter)) and value.annotation=='sort_order' and self.catalog.language_version<5:
                self.fail('LANGUAGE_INVALID','SORT_ORDER_VERSION','sort_order requires Pine v5/v6; v4 order is bool.',value.span)
            if isinstance(value, (parser.Declaration, parser.Parameter)) and value.annotation and value.annotation.startswith('array<') and self.catalog.language_version < 5:
                self.fail('LANGUAGE_INVALID','ARRAY_SYNTAX_VERSION','Generic array annotations require Pine v5 or v6.',value.span)
            if isinstance(value, parser.For) and self.catalog.language_version < 2 or isinstance(value, parser.While) and self.catalog.language_version < 5:
                self.fail("LANGUAGE_INVALID", "LOOP_VERSION", "This loop is unavailable in the selected Pine version.", value.span)
        # Syntax sugar can create long case chains inside a shallow source block.
        # Bound the normalized AST before any recursive resolver/template walk.
        pending, reviewed = [(statements, 0)], 0
        while pending:
            value, depth = pending.pop()
            if depth > 128:
                self.fail('PROFILE_UNCOVERED', 'FUNCTION_EXPANSION', 'Normalized syntax exceeds depth 128.', getattr(value, 'span', empty_span))
            review(value)
            if is_dataclass(value) and not isinstance(value, SourceSpan):
                reviewed += 1
                if reviewed > 8192:
                    self.fail('PROFILE_UNCOVERED', 'FUNCTION_EXPANSION', 'Normalized syntax exceeds 8192 nodes.', getattr(value, 'span', empty_span))
                pending.extend((getattr(value, item.name), depth + 1) for item in reversed(fields(value)) if item.name != 'span')
            elif isinstance(value, tuple):
                pending.extend((item, depth) for item in reversed(value))
        from .enums import register as register_enums
        register_enums(self,statements)
        from .objects import register
        register(self,statements)
        for node in statements:
            if isinstance(node, parser.Function):
                function_key=self.function_key(node)
                if any(p.qualifier=='simple' and is_object(self.type_base(p.annotation)) for p in node.parameters):
                    self.fail('LANGUAGE_INVALID','UDT_QUALIFIER','Object parameters always require series qualification.',node.span)
                if function_key in self.shared['functions'] and self.catalog.language_version<5:
                    previous=self.shared['functions'][function_key]
                    a,b=self.function_header(previous),self.function_header(node)
                    code='DUPLICATE_FUNCTION' if (a.types,a.qualifiers,a.required)==(b.types,b.qualifiers,b.required) else 'FUNCTION_OVERLOAD_VERSION'
                    self.fail('LANGUAGE_INVALID',code,'Function overloads require Pine v5 or v6.',node.span)
                if not isinstance(node,parser.Method) and (node.name in self.catalog.signatures or node.name in self.catalog.references or node.name in {"if", "else", "var", "na", "true", "false"}):
                    self.fail("LANGUAGE_INVALID", "FUNCTION_NAME", "Function name conflicts with a builtin.", node.span)
                if len(node.parameters) > 16 or len(self.shared['definitions']) >= 64:
                    self.fail("PROFILE_UNCOVERED", "FUNCTION_LIMIT", "Function definition/parameter limit exceeded.", node.span)
                if len({p.name for p in node.parameters}) != len(node.parameters):
                    self.fail("LANGUAGE_INVALID", "DUPLICATE_PARAMETER", "Function parameters must be unique.", node.span)
                self.shared['functions'].setdefault(function_key,node)
                self.shared['groups'].setdefault(function_key,[]).append(node)
                self.shared['definitions'][site_id('function',node.span)]=node
        from packages.contracts.overloads import validate_headers,admit
        from .specification import ROOT
        for name,group in self.shared['groups'].items():
            if isinstance(group[0],parser.Method):
                from packages.contracts.methods import validate,admit as admit_methods
                try:admit_methods(ROOT,self.catalog.snapshot,self.catalog.language_version)
                except ValueError as e:self.fail('SPEC_UNRESOLVED','METHOD_PROFILE',str(e),group[-1].span)
                try:validate(tuple(self.function_header(n) for n in group),self.catalog.language_version)
                except ValueError as e:self.fail('LANGUAGE_INVALID',str(e),'Invalid method declarations.',group[-1].span)
            if len(group)<2:continue
            if len(group)>16:self.fail('PROFILE_UNCOVERED','FUNCTION_OVERLOAD_LIMIT','At most 16 overloads per name.',group[-1].span)
            try:admit(ROOT,self.catalog.snapshot,self.catalog.language_version)
            except ValueError as error:self.fail('SPEC_UNRESOLVED','FUNCTION_OVERLOAD_PROFILE',str(error),group[-1].span)
            try:
                validate_headers(tuple(self.function_header(n) for n in group),self.catalog.language_version)
            except ValueError as error:self.fail('LANGUAGE_INVALID',str(error),'Invalid or conflicting overload declarations.',group[-1].span)
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
        strategy = declarations[0].contract.operation in STRATEGY_DECLARATIONS
        has_drawings = any(check(bound, calls_only=True) for check in (
            uses_labels, uses_lines, uses_boxes, uses_tables, uses_linefills, uses_polylines))
        if not strategy and not has_drawings and not any(isinstance(statement.expression if isinstance(statement,BoundStore) else statement,BoundCall) and "emits_plot" in (statement.expression if isinstance(statement,BoundStore) else statement).contract.effects for statement in bound):
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
            if not isinstance(tail, (parser.Call, parser.ExpressionStatement, parser.If, parser.For, parser.While, parser.ForIn, parser.Switch)):
                self.fail("LANGUAGE_INVALID", "FUNCTION_RETURN", "Function requires a final scalar expression.", tail.span)
            if isinstance(tail, parser.If):
                check_return(tail.body)
                if tail.otherwise:
                    check_return(tail.otherwise)
            if isinstance(tail, parser.Switch):check_return(tail.body)

        for function_id, definition in self.shared['definitions'].items():
            name=definition.name
            for parameter in definition.parameters:
                if parameter.default is not None:self.parameter_default(parameter)
            check_return(definition.body)
            resolver = Resolver(self.fail, f"template:{function_id}/", True,(*self.catalog.references,*getattr(self.catalog,'color_constants',{})),self.shared['namespace_prefixes'],self.shared['qualified_names'])
            local = {p.name: BindingInfo(parser.Declaration(p.name, p.annotation, "parameter", None, p.span),
                f"template:{function_id}", namespace=f"template:{function_id}/") for p in definition.parameters}
            resolver.statements(definition.body, [self.shared['captures'][function_id], local], f"template:{function_id}", True)
            graph[function_id] = set()
            for node in walk(definition.body):
                known_variable = isinstance(node, parser.Reference) and any(entry["name"] == node.name and entry["category"] == "variables" for entry in self.catalog.snapshot.references[self.catalog.language_version]["entries"])
                from .enums import member as enum_member
                known_enum=enum_member(self,node) is not None
                if isinstance(node, parser.Reference) and id(node) not in resolver.namespace_references and resolver.references.get(id(node)) is None and node.name not in self.catalog.references and node.name not in getattr(self.catalog,'color_constants',{}) and not known_variable and not known_enum:
                    self.fail("LANGUAGE_INVALID", "UNDEFINED_VARIABLE", f"No visible function variable {node.name!r}.", node.span, "rule.scope")
                if isinstance(node, parser.Call):
                    if id(node) in resolver.method_calls:
                        from .methods import static_edges
                        graph[function_id].update(static_edges(self,node,definition,function_id,resolver,local))
                        continue
                    if resolver.call_variables.get(id(node)):
                        self.fail("LANGUAGE_INVALID", "CALL_VARIABLE", "A function parameter/variable cannot be called.", node.span)
                    if node.name in self.shared["functions"]:
                        from packages.contracts.overloads import bind_positions,choose,base
                        group=self.visible_functions(node.name,function_id)
                        names=tuple(a.name for a in node.arguments)
                        shaped=[n for n in group if bind_positions(self.function_header(n),names)[1] is None]
                        if len(self.shared['groups'][node.name])==1:
                            graph[function_id].add(site_id('function',group[0].span))
                        elif len(shaped)==1:
                            graph[function_id].add(site_id('function',shaped[0].span))
                        else:
                            # Definite template edges use declared parameter/literal
                            # types only. Other expressions are resolved by the same
                            # Binder at actual written calls, including dead branches.
                            types=[]
                            params={p.name:p for p in definition.parameters}
                            for argument in node.arguments:
                                expression=argument.expression
                                if (isinstance(expression,parser.Reference) and expression.name in params
                                        and resolver.references.get(id(expression)) is local[expression.name]):
                                    p=params[expression.name]
                                    types.append(ValueType(self.type_base(base(p.annotation)),'series' if local[p.name].mutable else p.qualifier or 'series') if p.annotation else None)
                                elif isinstance(expression,parser.ColorLiteral):types.append(ValueType('color'))
                                elif isinstance(expression,parser.Literal):types.append(ValueType('na') if expression.value is None else literal_type(expression.value))
                                else:types.append(None)
                            if all(t is not None for t in types):
                                try:header,_=choose(tuple(self.function_header(n) for n in shaped),names,tuple(types),self.catalog.language_version)
                                except ValueError:pass
                                else:graph[function_id].add(header.function_id)
                    elif node.name.rpartition('.')[0] in self.shared.get('object_definitions',{}):
                        if node.name.rpartition('.')[2] not in ('new','copy'):self.fail('PROFILE_UNCOVERED','UDT_OPERATION','Unknown object operation.',node.span)
                    elif node.name not in self.catalog.signatures and not node.name.startswith(('array.','map.','matrix.')):
                        self.fail("LANGUAGE_INVALID", "SIGNATURE_UNAVAILABLE", "Unknown function in a function body.", node.span)
                    if any(set(s["effects"]) & {"declaration", "emits_plot", "configures_risk"} for s, _ in self.catalog.signatures.get(node.name, ())):
                        self.fail("LANGUAGE_INVALID", "GLOBAL_ONLY_CALL", "Declaration and plot calls cannot occur inside functions.", node.span)
        visiting, visited = set(), set()

        def visit(function_id):
            if function_id in visiting:
                self.fail("LANGUAGE_INVALID", "FUNCTION_RECURSION", "Recursive function dependency.", self.shared['definitions'][function_id].span)
            if function_id in visited:
                return
            visiting.add(function_id)
            for child in sorted(graph[function_id]):
                visit(child)
            visiting.remove(function_id)
            visited.add(function_id)

        for name in graph:
            visit(name)
