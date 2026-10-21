"""Pinned market contexts and the bounded historical request profile."""

from __future__ import annotations

import re
from dataclasses import dataclass,field

from .model import RunResult
from .storage import DatasetIdentity


def timeframe_ms(value):
    if type(value) is not str or not re.fullmatch(r"[1-9][0-9]{0,3}",value) or int(value)>1440:
        raise ValueError("This context profile requires a fixed minute timeframe in 1..1440.")
    return int(value)*60000


@dataclass(frozen=True)
class MarketContext:
    context_id: str
    symbol: str
    timeframe: str
    source: str
    timezone: str = "UTC"
    session: str = "24x7"
    time_rule: str = "fixed-duration-utc.1"

    def __post_init__(self):
        if (any(type(v) is not str or not 0<len(v)<=256 for v in (self.context_id,self.symbol,self.source))
                or ":" not in self.symbol or self.timezone!="UTC" or self.session!="24x7"
                or self.time_rule!="fixed-duration-utc.1"):
            raise ValueError("Contexts require an explicit qualified symbol/source and supported UTC/24x7 time rules.")
        timeframe_ms(self.timeframe)

    @classmethod
    def from_payload(cls,value):
        if type(value) is not dict or value.keys()!=set(cls.__dataclass_fields__):raise ValueError("Invalid market context fields.")
        return cls(**value)


@dataclass(frozen=True)
class ContextBinding:
    context: MarketContext
    snapshot_id: str

    def __post_init__(self):
        if type(self.context) is not MarketContext or type(self.snapshot_id) is not str or not re.fullmatch(r"[0-9a-f]{64}",self.snapshot_id):
            raise ValueError("Invalid context snapshot binding.")

    @classmethod
    def from_payload(cls,value):
        if type(value) is not dict or value.keys()!={"context","snapshot_id"}:raise ValueError("Invalid context binding fields.")
        return cls(MarketContext.from_payload(value["context"]),value["snapshot_id"])


def request_policy(version):
    return {"language_version":version,"name":"security" if version<5 else "request.security",
        "parameters":["x","y","z","a"] if version<3 else
            ["symbol","resolution" if version<5 else "timeframe","expression","gaps","lookahead"],
        "named":version>=3,"flags":"bool" if version<5 else "named_constant",
        "gaps_default":"off","lookahead_default":"on" if version<3 else "off",
        "expression":"close_or_ema_close_literal_length","scope":"unconditional_global",
        "contexts":"explicit_snapshots","timeframes":"fixed_minutes_1_1440",
        "request_direction":"same_or_higher","alignment":"exact_multiple_utc_epoch",
        "gaps_off":"carry_last_available_including_na","gaps_on":"only_new_availability_event",
        "availability_off":"requested_close_lte_primary_close","availability_on":"requested_open_lte_primary_open",
        "tie_order":"requested_before_primary","max_contexts":8,"max_requests":16}


@dataclass(frozen=True)
class ContextIdentity:
    context: MarketContext
    snapshot_id: str
    dataset: DatasetIdentity


@dataclass(frozen=True,kw_only=True)
class ContextRunResult(RunResult):
    primary_context: MarketContext
    contexts: tuple[ContextIdentity,...]
    requests: tuple[RequestIdentity,...]
    schema_version: str = field(default="openpine.run-result.3",init=False)


@dataclass(frozen=True)
class RequestIdentity:
    call_site_id: str
    context_id: str
    gaps: str
    lookahead: str
    historical_future: bool
