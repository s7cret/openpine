"""Immutable values crossing compiler, execution and runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LITERAL_PROFILE_ID = "openpine.literal-harness.1"
HISTORICAL_PROFILE_ID = "openpine.historical-bars.1"


@dataclass(frozen=True)
class SourceSpan:
    source: str
    start: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class Diagnostic:
    category: str
    code: str
    message: str
    span: SourceSpan | None = None
    rule_id: str | None = None
    language_version: int | None = None


class OpenPineError(ValueError):
    def __init__(self, diagnostic: Diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


@dataclass(frozen=True)
class ValueType:
    base: Literal["int", "float", "bool", "string", "void"]
    qualifier: Literal["const", "input", "simple", "series"] = "const"


@dataclass(frozen=True)
class TupleType:
    elements: tuple[ValueType, ...]

    @property
    def base(self): return 'tuple'

    @property
    def qualifier(self):
        return max((e.qualifier for e in self.elements), key=('const','input','simple','series').index)


@dataclass(frozen=True)
class TupleIR:
    elements: tuple[NodeIR, ...]
    value_type: TupleType
    span: SourceSpan
    kind: str = field(default='tuple_result', init=False)


@dataclass(frozen=True)
class UnpackIR:
    bindings: tuple[VariableContract | None, ...]
    expression: NodeIR
    span: SourceSpan
    kind: str = field(default='unpack', init=False)


@dataclass(frozen=True)
class ProgramIdentity:
    source_sha256: str
    language_version: int
    spec_manifest_sha256: str
    spec_lock_sha256: str
    semantic_sha256: str
    semantic_profile_id: str
    implementation_profile_id: str
    implementation_sha256: str
    compatibility_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiteralIR:
    value: int | float | bool | str
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="literal", init=False)


@dataclass(frozen=True)
class LanguagePolicy:
    language_version: int
    integer_division: str
    logical_evaluation: str
    numeric_conditions: bool
    ordinary_initialization: str = "each_bar"
    persistent_initialization: str = "first_execution"
    comparison_na: bool = False
    zero_division: str = "na"


@dataclass(frozen=True)
class NAIR:
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="na", init=False)


@dataclass(frozen=True)
class VariableContract:
    binding_id: str
    name: str
    value_type: ValueType
    mode: str
    scope_id: str
    span: SourceSpan
    namespace: str = ""


@dataclass(frozen=True)
class LoadIR:
    binding_id: str
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="load", init=False)


@dataclass(frozen=True)
class BinaryIR:
    operator: str
    left: NodeIR
    right: NodeIR
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="binary", init=False)


@dataclass(frozen=True)
class ConditionalIR:
    condition: NodeIR
    when_true: NodeIR
    when_false: NodeIR
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="conditional", init=False)


@dataclass(frozen=True)
class UnaryIR:
    operator: str
    operand: NodeIR
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="unary", init=False)


@dataclass(frozen=True)
class ReferenceContract:
    symbol_id: str
    assertion_id: str
    value_sha256: str
    slot: str
    value_type: ValueType


@dataclass(frozen=True)
class ReferenceIR:
    contract: ReferenceContract
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="reference", init=False)


@dataclass(frozen=True)
class HistoryIR:
    operand: NodeIR
    offset: NodeIR
    series_id: str
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="history", init=False)


@dataclass(frozen=True)
class ConvertIR:
    operand: NodeIR
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="convert", init=False)


@dataclass(frozen=True)
class ArgumentIR:
    parameter_id: str
    expression: NodeIR
    origin: Literal["source", "default"]
    source_index: int | None


@dataclass(frozen=True)
class CallIR:
    symbol_id: str
    overload_id: str
    assertion_id: str
    signature_sha256: str
    operation: str
    arguments: tuple[ArgumentIR, ...]
    evaluation_order: tuple[int, ...]
    effects: tuple[str, ...]
    value_type: ValueType | TupleType
    call_site_id: str
    span: SourceSpan
    kind: str = field(default="call", init=False)


@dataclass(frozen=True)
class BlockIR:
    statements: tuple[StatementIR, ...]
    result: NodeIR
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="block_value", init=False)


@dataclass(frozen=True)
class ParameterContract:
    name: str
    annotation: str | None
    qualifier: str | None
    span: SourceSpan


@dataclass(frozen=True)
class FunctionContract:
    function_id: str
    name: str
    parameters: tuple[ParameterContract, ...]
    captures: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class UserCallIR:
    function_id: str
    call_site_id: str
    parameters: tuple[VariableContract, ...]
    arguments: tuple[ArgumentIR, ...]
    evaluation_order: tuple[int, ...]
    statements: tuple[StatementIR, ...]
    result: NodeIR
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="user_call", init=False)


@dataclass(frozen=True)
class EvaluateIR:
    expression: NodeIR
    span: SourceSpan
    kind: str = field(default="evaluate", init=False)


@dataclass(frozen=True)
class InputDefinition:
    input_id: str
    name: str
    title: str
    input_type: str
    default: int | float | bool | str
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    options: tuple[int | float | bool | str, ...] = ()
    tooltip: str = ""
    group: str = ""
    inline: str = ""
    active: bool = True
    profile_id: str = ""


@dataclass(frozen=True)
class InputIR:
    definition: InputDefinition
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="input", init=False)


@dataclass(frozen=True)
class ForIR:
    loop_id: str
    counter: VariableContract
    initial: NodeIR
    final: NodeIR
    step: NodeIR
    statements: tuple[StatementIR, ...]
    result: NodeIR | None
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="for", init=False)


@dataclass(frozen=True)
class WhileIR:
    loop_id: str
    condition: NodeIR
    statements: tuple[StatementIR, ...]
    result: NodeIR | None
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="while", init=False)


@dataclass(frozen=True)
class LoopControlIR:
    action: Literal["break", "continue"]
    loop_id: str
    span: SourceSpan
    kind: str = field(default="loop_control", init=False)


type NodeIR = LiteralIR | NAIR | LoadIR | BinaryIR | ConditionalIR | UnaryIR | ReferenceIR | HistoryIR | ConvertIR | CallIR | BlockIR | UserCallIR | InputIR | TupleIR | ForIR | WhileIR


@dataclass(frozen=True)
class StoreIR:
    binding: VariableContract
    expression: NodeIR
    declaration: bool
    span: SourceSpan
    kind: str = field(default="store", init=False)


@dataclass(frozen=True)
class IfIR:
    condition: NodeIR
    body: tuple[StatementIR, ...]
    otherwise: tuple[StatementIR, ...]
    span: SourceSpan
    kind: str = field(default="if", init=False)


type StatementIR = CallIR | StoreIR | IfIR | EvaluateIR | UnpackIR | LoopControlIR


@dataclass(frozen=True)
class ProgramIR:
    identity: ProgramIdentity
    statements: tuple[StatementIR, ...]
    bindings: tuple[VariableContract, ...] = ()
    language_policy: LanguagePolicy | None = None
    functions: tuple[FunctionContract, ...] = ()
    schema_version: str = "openpine.typed-ir.5"


@dataclass(frozen=True)
class CallContract:
    symbol_id: str
    overload_id: str
    assertion_id: str
    signature_sha256: str
    operation: str
    parameter_ids: tuple[str, ...]
    parameter_types: tuple[tuple[str, ...], ...]
    qualifier_limits: tuple[str, ...]
    result_parameter: int | None
    effects: tuple[str, ...]
    result_type: ValueType | TupleType = ValueType("void")
    result_rule: str = "fixed"


@dataclass(frozen=True)
class PlotOutput:
    output_id: str
    title: str
    value_type: ValueType
    values: tuple[int | float | None, ...]


@dataclass(frozen=True)
class HistoricalBar:
    time: int
    close: float | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class HistoricalData:
    context_id: str
    symbol: str
    timeframe: str
    bars: tuple[HistoricalBar, ...]
    schema_version: str = field(default="openpine.historical-bars.1", kw_only=True)


@dataclass(frozen=True)
class BarFrame:
    context_id: str
    bar_index: int
    time: int
    close: float | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class DataIdentity:
    sha256: str
    context_id: str
    symbol: str
    timeframe: str
    times: tuple[int, ...]


@dataclass(frozen=True)
class IndicatorDeclaration:
    title: str
    shorttitle: str
    overlay: bool


@dataclass(frozen=True)
class RunResult:
    identity: ProgramIdentity
    program_sha256: str
    declaration: IndicatorDeclaration
    outputs: tuple[PlotOutput, ...]
    observation_count: int = 1
    data: DataIdentity | None = None
    schema_version: str = field(default="openpine.run-result.2", init=False)
