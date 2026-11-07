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
    base: str
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
class OverloadedFunctionContract(FunctionContract):
    required_parameters: tuple[bool, ...]


@dataclass(frozen=True)
class MethodContract(OverloadedFunctionContract):
    pass


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


@dataclass(frozen=True)
class ArrayIR:
    operation: Literal["new", "from", "size", "get", "set", "push", "pop", "shift", "unshift", "clear", "copy", "sum", "avg", "min", "max", "is_na", "insert", "remove", "reverse", "fill", "includes", "indexof", "lastindexof", "concat", "sort", "sort_indices", "binary_search", "binary_search_leftmost", "binary_search_rightmost", "slice"]
    element: str
    arguments: tuple[ArgumentIR, ...]
    evaluation_order: tuple[int, ...]
    value_type: ValueType
    call_site_id: str
    span: SourceSpan
    kind: str = field(default="array_operation", init=False)


@dataclass(frozen=True)
class MatrixIR:
    operation: Literal["new","get","set","fill","rows","columns","elements_count","copy","row","col","add_row","add_col","remove_row","remove_col","swap_rows","swap_columns","reshape","transpose","is_na"]
    element: str
    arguments: tuple[ArgumentIR, ...]
    evaluation_order: tuple[int, ...]
    value_type: ValueType
    call_site_id: str
    span: SourceSpan
    kind: str = field(default="matrix_operation", init=False)


@dataclass(frozen=True)
class MapIR:
    operation: Literal["new","put","put_all","get","contains","remove","clear","size","copy","keys","values","is_na"]
    key_type: str
    element: str
    arguments: tuple[ArgumentIR, ...]
    evaluation_order: tuple[int, ...]
    value_type: ValueType
    call_site_id: str
    span: SourceSpan
    kind: str = field(default="map_operation", init=False)


@dataclass(frozen=True)
class ForInIR:
    loop_id: str
    targets: tuple[VariableContract | None, ...]
    collection: NodeIR
    statements: tuple[StatementIR, ...]
    result: NodeIR | None
    scope_id: str
    value_type: ValueType | TupleType
    span: SourceSpan
    kind: str = field(default="for_in", init=False)


@dataclass(frozen=True)
class ObjectIR:
    operation: Literal['new', 'copy', 'get', 'set', 'is_na']
    type_id: str
    field_index: int | None
    arguments: tuple[ArgumentIR, ...]
    evaluation_order: tuple[int, ...]
    value_type: ValueType
    call_site_id: str
    span: SourceSpan
    kind: str = field(default='object_operation', init=False)


@dataclass(frozen=True)
class EnumIR:
    operation: Literal['title','is_na']
    operand: NodeIR
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default='enum_operation', init=False)


type NodeIR = LiteralIR | NAIR | LoadIR | BinaryIR | ConditionalIR | UnaryIR | ReferenceIR | HistoryIR | ConvertIR | CallIR | BlockIR | UserCallIR | InputIR | TupleIR | ForIR | WhileIR | ArrayIR | MapIR | MatrixIR | ForInIR | ObjectIR | EnumIR


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
    functions: tuple[FunctionContract | OverloadedFunctionContract | MethodContract, ...] = ()
    schema_version: str = "openpine.typed-ir.5"


@dataclass(frozen=True)
class ObjectFieldContract:
    name: str
    base: str
    intrabar: bool
    default: NodeIR
    span: SourceSpan


@dataclass(frozen=True)
class ObjectTypeContract:
    type_id: str
    name: str
    fields: tuple[ObjectFieldContract, ...]
    span: SourceSpan


@dataclass(frozen=True)
class ObjectProgramIR(ProgramIR):
    schema_version: str = 'openpine.typed-ir.14'
    object_types: tuple[ObjectTypeContract, ...] = ()


@dataclass(frozen=True)
class EnumMemberContract:
    name: str
    title: str
    span: SourceSpan


@dataclass(frozen=True)
class EnumTypeContract:
    type_id: str
    name: str
    members: tuple[EnumMemberContract, ...]
    span: SourceSpan


@dataclass(frozen=True)
class EnumProgramIR(ObjectProgramIR):
    schema_version: str = 'openpine.typed-ir.16'
    enum_types: tuple[EnumTypeContract, ...] = ()


@dataclass(frozen=True)
class EnumInputDefinition(InputDefinition):
    enum_name: str = ''
    member_names: tuple[str, ...] = ()
    member_titles: tuple[str, ...] = ()


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
class StyledPlotOutput(PlotOutput):
    colors: tuple[str | None, ...]
    linewidth: int
    style: str
    presentation_schema: str = field(default='openpine.plot-presentation.1',init=False)


@dataclass(frozen=True)
class VisualLayerOutput(StyledPlotOutput):
    kind: str = 'plot'
    references: tuple[str,...] = ()
    fillgaps: bool = False
    presentation_schema: str = field(default='openpine.visual-layer.1',init=False)

@dataclass(frozen=True)
class SignalLayerOutput(StyledPlotOutput):
    kind: str = 'plotshape'
    location: str = 'abovebar'
    size: str = 'auto'
    offset: int = 0
    text: str = ''
    char: str = ''
    textcolors: tuple[str | None, ...] = ()
    presentation_schema: str = field(default='openpine.signal-layer.1',init=False)


@dataclass(frozen=True, kw_only=True)
class OverlayPlotOutput(StyledPlotOutput):
    force_overlay: Literal[True]


@dataclass(frozen=True, kw_only=True)
class OverlayVisualLayerOutput(VisualLayerOutput):
    force_overlay: Literal[True]


@dataclass(frozen=True, kw_only=True)
class OverlaySignalLayerOutput(SignalLayerOutput):
    force_overlay: Literal[True]

@dataclass(frozen=True, kw_only=True)
class WindowedSignalLayerOutput(SignalLayerOutput):
    show_last: int


@dataclass(frozen=True, kw_only=True)
class OverlayWindowedSignalLayerOutput(WindowedSignalLayerOutput):
    force_overlay: Literal[True]


@dataclass(frozen=True, kw_only=True)
class ArrowLayerOutput(SignalLayerOutput):
    minheight: int
    maxheight: int
    max_abs: float
    show_last: int | None = None


@dataclass(frozen=True, kw_only=True)
class OverlayArrowLayerOutput(ArrowLayerOutput):
    force_overlay: Literal[True]


@dataclass(frozen=True)
class OHLCPoint:
    open: float
    high: float
    low: float
    close: float
    color: str | None
    wickcolor: str | None
    bordercolor: str | None


@dataclass(frozen=True)
class OHLCPlotOutput(PlotOutput):
    kind: Literal['plotbar', 'plotcandle']
    bars: tuple[OHLCPoint | None, ...]
    presentation_schema: str = field(default='openpine.ohlc-layer.1', init=False)


@dataclass(frozen=True, kw_only=True)
class OverlayOHLCPlotOutput(OHLCPlotOutput):
    force_overlay: Literal[True]


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


@dataclass(frozen=True, kw_only=True)
class LabelRecord:
    id: int
    x: int | None
    y: float | None
    text: str


@dataclass(frozen=True, kw_only=True)
class LocatedLabelRecord(LabelRecord):
    xloc: str


@dataclass(frozen=True, kw_only=True)
class StyledLabelRecord(LocatedLabelRecord):
    color: str | None
    style: str
    textcolor: str | None
    size: str | int


@dataclass(frozen=True, kw_only=True)
class VerticalLabelRecord(LocatedLabelRecord):
    yloc: str


@dataclass(frozen=True, kw_only=True)
class StyledVerticalLabelRecord(StyledLabelRecord):
    yloc: str


@dataclass(frozen=True, kw_only=True)
class LabelTextAttributes:
    textalign: str
    tooltip: str
    text_font_family: str
    text_formatting: int


@dataclass(frozen=True, kw_only=True)
class TextLabelRecord(VerticalLabelRecord, LabelTextAttributes):
    textalign: str | None


@dataclass(frozen=True, kw_only=True)
class StyledTextLabelRecord(StyledVerticalLabelRecord, LabelTextAttributes):
    pass


@dataclass(frozen=True, kw_only=True)
class OverlayLabelRecord(StyledTextLabelRecord):
    force_overlay: Literal[True]


LabelSnapshot = (LabelRecord | LocatedLabelRecord | StyledLabelRecord | VerticalLabelRecord
                 | StyledVerticalLabelRecord | TextLabelRecord | StyledTextLabelRecord | OverlayLabelRecord)


@dataclass(frozen=True, kw_only=True)
class LabelRunResult(RunResult):
    labels: tuple[LabelSnapshot, ...]


@dataclass(frozen=True, kw_only=True)
class LineRecord:
    id: int
    x1: int | None
    y1: float | None
    x2: int | None
    y2: float | None


@dataclass(frozen=True, kw_only=True)
class ExtendedLineRecord(LineRecord):
    extend: str


@dataclass(frozen=True, kw_only=True)
class LocatedLineRecord(ExtendedLineRecord):
    xloc: str


@dataclass(frozen=True, kw_only=True)
class StyledLineRecord(LocatedLineRecord):
    color: str | None
    style: str
    width: int


@dataclass(frozen=True, kw_only=True)
class OverlayLineRecord(StyledLineRecord):
    force_overlay: Literal[True]


type LineSnapshot = LineRecord | ExtendedLineRecord | LocatedLineRecord | StyledLineRecord | OverlayLineRecord


@dataclass(frozen=True, kw_only=True)
class LineRunResult(LabelRunResult):
    lines: tuple[LineSnapshot, ...]


@dataclass(frozen=True, kw_only=True)
class BoxRecord:
    id: int
    left: int | None
    top: float | None
    right: int | None
    bottom: float | None
    border_color: str | None
    bgcolor: str | None


@dataclass(frozen=True, kw_only=True)
class LocatedBoxRecord(BoxRecord):
    xloc: str


@dataclass(frozen=True, kw_only=True)
class StyledBoxRecord(LocatedBoxRecord):
    border_width: int
    border_style: str
    extend: str


@dataclass(frozen=True, kw_only=True)
class TextBoxRecord(StyledBoxRecord):
    text: str
    text_size: str | int
    text_color: str | None
    text_halign: str
    text_valign: str
    text_wrap: str
    text_font_family: str
    text_formatting: int


@dataclass(frozen=True, kw_only=True)
class OverlayBoxRecord(TextBoxRecord):
    force_overlay: Literal[True]


type BoxSnapshot = BoxRecord | LocatedBoxRecord | StyledBoxRecord | TextBoxRecord | OverlayBoxRecord


@dataclass(frozen=True, kw_only=True)
class BoxRunResult(LineRunResult):
    boxes: tuple[BoxSnapshot, ...]


@dataclass(frozen=True, slots=True)
class TableCell:
    column: int
    row: int
    text: str
    text_color: str | None
    bgcolor: str | None


@dataclass(frozen=True, slots=True)
class TableRecord:
    id: int
    position: str
    columns: int
    rows: int
    bgcolor: str | None
    cells: tuple[TableCell, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OverlayTableRecord(TableRecord):
    force_overlay: Literal[True]


type TableSnapshot = TableRecord | OverlayTableRecord


@dataclass(frozen=True, kw_only=True)
class TableRunResult(BoxRunResult):
    tables: tuple[TableSnapshot, ...]


@dataclass(frozen=True, kw_only=True)
class LineFillRecord:
    id: int
    line1: int
    line2: int
    color: str | None


@dataclass(frozen=True, kw_only=True)
class LineFillRunResult(TableRunResult):
    linefills: tuple[LineFillRecord, ...]


@dataclass(frozen=True, kw_only=True)
class PolylineRecord:
    id: int
    vertices: tuple[tuple[int, float], ...]
    closed: bool
    xloc: str
    line_color: str | None
    fill_color: str | None


@dataclass(frozen=True, kw_only=True)
class OverlayPolylineRecord(PolylineRecord):
    force_overlay: Literal[True]


@dataclass(frozen=True, kw_only=True)
class StyledPolylineRecord(PolylineRecord):
    line_style: str
    line_width: int


@dataclass(frozen=True, kw_only=True)
class StyledOverlayPolylineRecord(StyledPolylineRecord):
    force_overlay: Literal[True]


type PolylineSnapshot = PolylineRecord | OverlayPolylineRecord | StyledPolylineRecord | StyledOverlayPolylineRecord


@dataclass(frozen=True, kw_only=True)
class PolylineRunResult(LineFillRunResult):
    polylines: tuple[PolylineSnapshot, ...]
