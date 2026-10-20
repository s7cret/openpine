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
class ProgramIdentity:
    source_sha256: str
    language_version: int
    spec_manifest_sha256: str
    spec_lock_sha256: str
    semantic_sha256: str
    semantic_profile_id: str
    implementation_profile_id: str
    implementation_sha256: str


@dataclass(frozen=True)
class LiteralIR:
    value: int | float | bool | str
    value_type: ValueType
    span: SourceSpan
    kind: str = field(default="literal", init=False)


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
class ConvertIR:
    operand: NodeIR
    value_type: ValueType
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
    value_type: ValueType
    call_site_id: str
    span: SourceSpan
    kind: str = field(default="call", init=False)


type NodeIR = LiteralIR | UnaryIR | ReferenceIR | ConvertIR | CallIR


@dataclass(frozen=True)
class ProgramIR:
    identity: ProgramIdentity
    statements: tuple[CallIR, ...]
    schema_version: str = field(default="openpine.typed-ir.2", init=False)


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
    result_type: ValueType = ValueType("void")


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


@dataclass(frozen=True)
class HistoricalData:
    context_id: str
    symbol: str
    timeframe: str
    bars: tuple[HistoricalBar, ...]
    schema_version: str = field(default="openpine.historical-bars.1", init=False)


@dataclass(frozen=True)
class BarFrame:
    context_id: str
    bar_index: int
    time: int
    close: float | None


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
