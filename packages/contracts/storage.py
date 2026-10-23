"""Versioned storage identities and process resource policy. No database imports."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass,asdict,fields
from typing import Protocol


@dataclass(frozen=True)
class DatasetIdentity:
    sha256: str
    context_id: str
    symbol: str
    timeframe: str
    count: int
    first_ms: int
    last_ms: int
    hash_scheme: str = "openpine.candle-blocks.1"


@dataclass(frozen=True)
class ResourcePolicy:
    workers: int = 2
    threads_per_worker: int = 1
    memory_mb_per_worker: int = 512
    total_memory_mb: int = 1024
    max_attempts: int = 1
    heartbeat_timeout: float = 15.0

    def __post_init__(self):
        if (any(type(v) is not int for v in (self.workers, self.threads_per_worker,
                self.memory_mb_per_worker, self.total_memory_mb, self.max_attempts))
                or not 1 <= self.workers <= 32 or not 1 <= self.threads_per_worker <= 8
                or not 128 <= self.memory_mb_per_worker <= self.total_memory_mb
                or self.workers * self.memory_mb_per_worker > self.total_memory_mb
                or not 1 <= self.max_attempts <= 3 or not 2 <= self.heartbeat_timeout <= 120):
            raise ValueError("Invalid aggregate worker resource policy.")


@dataclass(frozen=True)
class RunPlan:
    input_ref: str
    snapshot_id: str
    timeout_seconds: float
    execution_sha256: str
    spec_lock_sha256: str
    profile_id: str = "openpine.historical-bars.1"
    threads: int = 1
    memory_mb: int = 512
    max_attempts: int = 1
    heartbeat_timeout: float = 15.0
    output_schema: str = "openpine.run-summary.3"
    checkpoint_interval_bars: int = 100000
    primary_context: MarketContext | None = None
    contexts: tuple[ContextBinding,...] = ()
    simulation_policy: BrokerPolicy | None = None
    schema_version: str = "openpine.run-plan.4"
    settings: dict | None = None
    evaluation_window: dict | None = None

    def __post_init__(self):
        if (any(type(x) is not str or not re.fullmatch(r"[0-9a-f]{64}", x)
                for x in (self.input_ref, self.snapshot_id, self.execution_sha256, self.spec_lock_sha256))
                or type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 3600
                or type(self.heartbeat_timeout) not in (int, float) or not 2 <= self.heartbeat_timeout <= 120
                or not math.isfinite(self.heartbeat_timeout)
                or any(type(x) is not int for x in (self.threads, self.memory_mb, self.max_attempts))
                or not 1 <= self.threads <= 8 or not 128 <= self.memory_mb <= 1_048_576
                or not 1 <= self.max_attempts <= 3 or self.profile_id != "openpine.historical-bars.1"
                or type(self.checkpoint_interval_bars) is not int or not 0<=self.checkpoint_interval_bars<=10_000_000
                or self.output_schema not in ("openpine.run-summary.3","openpine.run-summary.4","openpine.run-summary.5","openpine.run-summary.6","openpine.run-summary.7") or self.schema_version not in ("openpine.run-plan.1","openpine.run-plan.2","openpine.run-plan.3","openpine.run-plan.4","openpine.run-plan.5","openpine.run-plan.6")
                or self.schema_version=="openpine.run-plan.1" and self.checkpoint_interval_bars!=0):
            raise ValueError("Invalid immutable run plan.")
        from .contexts import MarketContext,ContextBinding
        if (self.primary_context is not None and type(self.primary_context) is not MarketContext
                or type(self.contexts) is not tuple or len(self.contexts)>7
                or any(type(c) is not ContextBinding for c in self.contexts)
                or self.contexts and self.primary_context is None
                or self.schema_version not in ("openpine.run-plan.3","openpine.run-plan.4","openpine.run-plan.5","openpine.run-plan.6") and (self.primary_context is not None or self.contexts)):
            raise ValueError("Invalid run plan contexts.")
        members=[c.context for c in self.contexts]+([self.primary_context] if self.primary_context else [])
        if len({c.context_id for c in members})!=len(members) or len({(c.symbol,c.timeframe) for c in members})!=len(members):
            raise ValueError("Run plan contains ambiguous context identities.")
        from .trading import BrokerPolicy
        if self.simulation_policy is not None and (type(self.simulation_policy) is not BrokerPolicy or self.schema_version not in ('openpine.run-plan.4','openpine.run-plan.5','openpine.run-plan.6')):
            raise ValueError('Invalid simulation plan policy.')
        if self.schema_version in ('openpine.run-plan.5','openpine.run-plan.6'):
            from .inputs import validate_settings
            if self.schema_version!='openpine.run-plan.6' or self.settings is not None:validate_settings(self.settings)
        elif self.settings is not None:raise ValueError('Settings require run plan .5.')
        if self.schema_version=='openpine.run-plan.6':
            from .optimization import evaluation_window
            evaluation_window(self.evaluation_window)
        elif self.evaluation_window is not None:raise ValueError('Evaluation window requires run plan .6.')
        expected='openpine.run-summary.7' if self.evaluation_window is not None else 'openpine.run-summary.6' if self.settings is not None else 'openpine.run-summary.5' if self.simulation_policy is not None else 'openpine.run-summary.4' if self.primary_context else 'openpine.run-summary.3'
        if self.output_schema!=expected:
            raise ValueError("Output schema differs from context plan.")

    def payload(self):
        value=asdict(self)
        if self.schema_version!='openpine.run-plan.6':value.pop('evaluation_window')
        if self.schema_version not in ('openpine.run-plan.5','openpine.run-plan.6'):value.pop('settings')
        if self.schema_version=="openpine.run-plan.1":value.pop("checkpoint_interval_bars")
        if self.schema_version not in ('openpine.run-plan.4','openpine.run-plan.5','openpine.run-plan.6'):value.pop('simulation_policy')
        if self.schema_version not in ("openpine.run-plan.3","openpine.run-plan.4","openpine.run-plan.5","openpine.run-plan.6"):
            value.pop("primary_context");value.pop("contexts")
        else:value["contexts"]=list(value["contexts"])
        return value

    @classmethod
    def from_payload(cls,value):
        if type(value) is not dict:raise ValueError("Invalid run plan record.")
        expected={f.name for f in fields(cls)}
        if value.get('schema_version')!='openpine.run-plan.6':expected.remove('evaluation_window')
        if value.get('schema_version') not in ('openpine.run-plan.5','openpine.run-plan.6'):expected.remove('settings')
        if value.get('schema_version') not in ('openpine.run-plan.4','openpine.run-plan.5','openpine.run-plan.6'):expected.remove('simulation_policy')
        if value.get("schema_version")=="openpine.run-plan.1":expected.remove("checkpoint_interval_bars")
        if value.get("schema_version") not in ("openpine.run-plan.3","openpine.run-plan.4","openpine.run-plan.5","openpine.run-plan.6"):expected.difference_update(("primary_context","contexts"))
        if value.keys()!=expected:raise ValueError("Missing or extra run plan fields.")
        if value.get("schema_version")=="openpine.run-plan.1":
            value={**value,"checkpoint_interval_bars":0}
        if value.get('schema_version') in ('openpine.run-plan.4','openpine.run-plan.5','openpine.run-plan.6'):
            from .trading import BrokerPolicy
            value={**value,'simulation_policy':BrokerPolicy.from_payload(value['simulation_policy']) if value['simulation_policy'] is not None else None}
        if value.get("schema_version") in ("openpine.run-plan.3","openpine.run-plan.4","openpine.run-plan.5","openpine.run-plan.6"):
            from .contexts import MarketContext,ContextBinding
            if type(value["contexts"]) is not list:raise ValueError("Context bindings require an array.")
            value={**value,"primary_context":MarketContext.from_payload(value["primary_context"]) if value["primary_context"] is not None else None,
                "contexts":tuple(ContextBinding.from_payload(c) for c in value["contexts"])}
        return cls(**value)


@dataclass(frozen=True)
class CoordinatorPolicy:
    memory_mb: int = 512
    base_mb: int = 128
    preparation_mb: int = 256
    read_mb: int = 128
    preparations: int = 1
    readers: int = 2
    connections: int = 8

    def __post_init__(self):
        if (any(type(v) is not int or v < 1 for v in self.__dict__.values())
                or self.base_mb + max(self.preparation_mb, self.read_mb) > self.memory_mb
                or self.connections > 64 or self.preparations > 8 or self.readers > 16):
            raise ValueError("Invalid coordinator resource policy.")


class BarSource(Protocol):
    identity: DatasetIdentity

    def __iter__(self): ...


class OutputSink(Protocol):
    def emit(self, output_id, title, value_type, value, index, timestamp): ...


class JobRepository(Protocol):
    def get(self, job_id): ...
    def list(self, limit=100): ...
    def active(self): ...
    def insert(self, state, request, *, imported_from=None, plan=None,experiment_variant=None): ...
    def experiment_owner(self,job_id): ...
    def experiment_desired(self,job_id): ...
    def plan(self, job_id): ...
    def attach_plan(self, job_id, plan): ...
    def request(self, job_id): ...
    def claim(self, state): ...
    def save(self, state): ...
    def finish(self, state): ...
    def retry(self, state, previous_attempt, error): ...
    def progress(self, state): ...
    def checkpoint(self,state,checkpoint_id,completed): ...
    def pause(self,state): ...
    def resume(self,state): ...
    def scanned(self, name): ...
    def record_import(self, path, job_id=None, error=None): ...
