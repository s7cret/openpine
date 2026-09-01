"""RC6 generated-script session bound to backtest-engine intents."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import sys
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ast2python.artifacts import verify_generated_artifact_v3
from backtest_engine import BacktestConfig
from backtest_engine.core.delegated_strategy_intents import (
    DelegatedStrategyIntentHandler,
    build_delegated_strategy_dispatcher,
)
from backtest_engine.core.intent_replay import IntentReplayIdentity
from openpine_contracts import (
    aggregate_batch_hash,
    seal_content_hash,
    validate_payload,
    validate_worker_protocol_sequence,
    verify_content_hash,
)
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
from pinelib.runtime.session import CallbackResult


_PROTOCOL_COMPONENT = {
    "HELLO": "openpine",
    "LOAD_ARTIFACT": "openpine",
    "INIT_RUN": "openpine",
    "BAR_BEGIN": "openpine",
    "INTENT_BATCH": "backtest_engine",
    "BROKER_EVENT_BATCH": "backtest_engine",
    "RECALC_REQUEST": "backtest_engine",
    "RECALC_RESULT": "pinelib",
    "BAR_COMMIT": "backtest_engine",
    "FINALIZE": "openpine",
    "ABORT": "openpine",
}
_PROTOCOL_ROLE = {
    "HELLO": "worker",
    "LOAD_ARTIFACT": "parent",
    "INIT_RUN": "parent",
    "BAR_BEGIN": "parent",
    "INTENT_BATCH": "worker",
    "BROKER_EVENT_BATCH": "engine",
    "RECALC_REQUEST": "engine",
    "RECALC_RESULT": "worker",
    "BAR_COMMIT": "engine",
    "FINALIZE": "parent",
    "ABORT": "parent",
}
_PROTOCOL_ROLES = {kind: {role} for kind, role in _PROTOCOL_ROLE.items()}
_PROTOCOL_ROLES["ABORT"] = {"parent", "worker", "engine"}
_PROTOCOL_AFTER = {
    "HELLO": {"LOAD_ARTIFACT", "ABORT"},
    "LOAD_ARTIFACT": {"INIT_RUN", "ABORT"},
    "INIT_RUN": {"BAR_BEGIN", "FINALIZE", "ABORT"},
    "BAR_BEGIN": {"INTENT_BATCH", "ABORT"},
    "INTENT_BATCH": {"BROKER_EVENT_BATCH", "BAR_COMMIT", "ABORT"},
    "BROKER_EVENT_BATCH": {"RECALC_REQUEST", "BAR_COMMIT", "ABORT"},
    "RECALC_REQUEST": {"RECALC_RESULT", "ABORT"},
    "RECALC_RESULT": {"INTENT_BATCH", "ABORT"},
    "BAR_COMMIT": {"BAR_BEGIN", "FINALIZE", "ABORT"},
    "FINALIZE": set(),
    "ABORT": set(),
}


def _semver(value: object) -> str:
    text = str(value)
    if "rc" in text and "-rc." not in text:
        base, marker, rc = text.partition("rc")
        if marker and base and rc.isdigit():
            return f"{base}-rc.{rc}"
    return text


class RC6WorkerProtocol:
    """Fail-closed protocol-v2 transcript bound to one execution context."""

    def __init__(self, execution_context: Mapping[str, Any]) -> None:
        self.context = deepcopy(dict(execution_context))
        validate_payload("openpine.execution_context.v1", self.context)
        if not verify_content_hash(
            self.context, schema_id="openpine.execution_context.v1"
        ):
            raise ValueError("execution context content hash is invalid")
        commits = self.context["producer_commits"]
        versions = {
            row["name"]: _semver(row["version"])
            for row in self.context["wheel_identities"]
        }
        self.identities = {
            component: (versions[component], commits[component])
            for component in {"openpine", "pinelib", "backtest_engine"}
        }
        self.messages: list[dict[str, Any]] = []

    @property
    def last_id(self) -> str | None:
        return None if not self.messages else self.messages[-1]["message_id"]

    def _transition(self, kind: str) -> None:
        if not self.messages and kind != "HELLO":
            raise ValueError("worker protocol must start with HELLO")
        if self.messages and kind not in _PROTOCOL_AFTER[self.messages[-1]["kind"]]:
            raise ValueError("invalid worker protocol transition")

    def append(
        self, kind: str, body: Mapping[str, Any], created_at_utc_ms: int
    ) -> dict[str, Any]:
        self._transition(kind)
        component = _PROTOCOL_COMPONENT[kind]
        version, commit = self.identities[component]
        sequence = len(self.messages)
        payload = seal_content_hash(
            {
                "schema_id": "openpine.worker.protocol.v2",
                "schema_version": "2.3.0",
                "producer": component,
                "producer_version": version,
                "producer_commit": commit,
                "stack_id": self.context["stack_manifest_hash"],
                "created_at_utc_ms": int(created_at_utc_ms),
                "serializer_id": "openpine.canonical.json.v1",
                "content_hash_alg": "sha256",
                "message_id": f"{self.context['session_id']}:{sequence}:{kind}",
                "sender_role": _PROTOCOL_ROLE[kind],
                "session_id": self.context["session_id"],
                "run_id": self.context["run_id"],
                "sequence": sequence,
                "correlation_id": self.context["run_id"],
                "causation_id": self.last_id,
                "kind": kind,
                "body": deepcopy(dict(body)),
            },
            schema_id="openpine.worker.protocol.v2",
        )
        validate_payload("openpine.worker.protocol.v2", payload)
        candidate = [*self.messages, payload]
        if kind in {"FINALIZE", "ABORT"}:
            validate_worker_protocol_sequence(candidate)
        self.messages.append(payload)
        return payload

    def accept(self, message: Mapping[str, Any]) -> dict[str, Any]:
        accepted = deepcopy(dict(message))
        validate_payload("openpine.worker.protocol.v2", accepted)
        if not verify_content_hash(
            accepted, schema_id="openpine.worker.protocol.v2"
        ):
            raise ValueError("worker protocol content hash is invalid")
        kind = accepted["kind"]
        self._transition(kind)
        role = accepted.get("sender_role")
        if role not in _PROTOCOL_ROLES[kind]:
            raise ValueError("worker protocol sender role mismatch")
        component = (
            "backtest_engine"
            if kind == "ABORT" and role == "engine"
            else _PROTOCOL_COMPONENT[kind]
        )
        version, commit = self.identities[component]
        expected = {
            "producer": component,
            "producer_version": version,
            "producer_commit": commit,
            "stack_id": self.context["stack_manifest_hash"],
            "session_id": self.context["session_id"],
            "run_id": self.context["run_id"],
            "sequence": len(self.messages),
            "correlation_id": self.context["run_id"],
            "causation_id": self.last_id,
            "sender_role": role,
        }
        if any(accepted.get(field) != value for field, value in expected.items()):
            raise ValueError("worker protocol identity mismatch")
        candidate = [*self.messages, accepted]
        if kind in {"FINALIZE", "ABORT"}:
            validate_worker_protocol_sequence(candidate)
        self.messages.append(accepted)
        return accepted


@dataclass(frozen=True, slots=True)
class RC6BarExecution:
    """One committed callback and its canonical engine-owned intents."""

    committed: CallbackResult
    intents: tuple[dict[str, Any], ...]


class RC6GeneratedScriptSession:
    """Execute persistent ``GeneratedScript(runtime).run()`` callbacks.

    The PineLib session owns series/runtime state. A fresh immutable delegated
    dispatcher binds the current broker projection before each callback, while
    backtest-engine owns conversion and sealing of committed strategy intents.
    """

    def __init__(
        self,
        *,
        artifact: Mapping[str, object],
        language: RuntimeLanguageContext,
        instrument: InstrumentContext,
        timeframe: TimeframeContext,
        identity: IntentReplayIdentity,
        producer_commit: str,
        default_qty_value: object = 1,
    ) -> None:
        envelope = artifact.get("generated_artifact")
        source = artifact.get("python_code")
        if not isinstance(envelope, Mapping) or not isinstance(source, str) or not source:
            raise ValueError("generated artifact envelope and source are required")
        validate_payload("openpine.generated_artifact.v3", envelope)
        verify_generated_artifact_v3(envelope)
        emitted_hash = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
        if envelope.get("emitted_module_hash") != emitted_hash:
            raise ValueError("generated artifact emitted module hash is invalid")
        entrypoint = envelope.get("entrypoint")
        if not isinstance(entrypoint, Mapping) or set(entrypoint) != {"module", "class"}:
            raise ValueError("generated artifact entrypoint is malformed")
        module_name = entrypoint.get("module")
        entrypoint_name = entrypoint.get("class")
        if not isinstance(module_name, str) or not isinstance(entrypoint_name, str):
            raise ValueError("generated artifact entrypoint identity is malformed")
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        expected_imports = envelope.get("import_manifest")
        if not isinstance(expected_imports, list) or sorted(imports) != expected_imports:
            raise ValueError("generated module imports differ from sealed import manifest")
        if any(
            imported.split(".", 1)[0] not in {"__future__", "pinelib", "typing"}
            for imported in imports
        ):
            raise ValueError("generated module imports a forbidden package")
        namespace: dict[str, Any] = {"__name__": module_name}
        exec(compile(tree, f"<{module_name}>", "exec"), namespace, namespace)
        generated_class = namespace.get(entrypoint_name)
        if not isinstance(generated_class, type):
            raise ValueError("generated artifact entrypoint class is unavailable")
        if tuple(inspect.signature(generated_class).parameters) != ("runtime",):
            raise ValueError("GeneratedScript constructor must accept exactly runtime")
        run_method = getattr(generated_class, "run", None)
        if not callable(run_method) or tuple(inspect.signature(run_method).parameters) != (
            "self",
        ):
            raise ValueError("GeneratedScript.run must accept only self")
        self.generated_class = generated_class
        self.namespace = MappingProxyType(namespace)
        self.session = RuntimeSession(
            language,
            instrument=instrument,
            timeframe=timeframe,
        )
        if not isinstance(identity, IntentReplayIdentity):
            raise TypeError("identity must be IntentReplayIdentity")
        self.identity = identity
        self.producer_commit = producer_commit
        self.intent_config = BacktestConfig(
            symbol=instrument.ticker,
            timeframe=identity.timeframe,
            start_time=0,
            end_time=0,
            default_qty_value=default_qty_value,
        )
        self._intent_sequence = 0

    def execute_bar(
        self,
        values: BarValues,
        *,
        bar_index: int,
        last_bar_index: int,
        strategy_values: Mapping[str, object],
        realtime: bool = False,
        tick_index: int = 0,
        final_tick: bool = True,
    ) -> RC6BarExecution:
        bar_time = values.time
        if type(bar_time) is not int or bar_time < 0:
            raise ValueError("bar time must be a nonnegative integer")
        handler = DelegatedStrategyIntentHandler(
            identity=self.identity,
            producer_commit=self.producer_commit,
            bar_open_time_utc_ms={bar_index: bar_time},
            config=self.intent_config,
        )
        self.session.delegated_dispatcher = (
            build_delegated_strategy_dispatcher(
                handler,
                strategy_values=strategy_values,
            )
        )
        transaction = self.session.begin(
            CallbackFrame(
                "REALTIME_EVAL" if realtime else "HISTORICAL_EVAL",
                self.session.sequence + 1,
                realtime=realtime,
                final_tick=final_tick,
                bar_index=bar_index,
                tick_index=tick_index,
                is_last_bar=bar_index == last_bar_index,
                is_last_confirmed_history=(
                    not realtime and bar_index == last_bar_index
                ),
                last_bar_index=last_bar_index,
            ),
            values=values,
        )
        try:
            instance = self.generated_class(transaction)
            instance.run()
            committed = transaction.commit()
        except Exception:
            if not transaction.closed:
                transaction.abort()
            raise
        intents = handler.seal_committed(
            [output.value for output in committed.delegated_outputs],
            start_sequence=self._intent_sequence,
        )
        self._intent_sequence += len(intents)
        return RC6BarExecution(committed=committed, intents=intents)


def _pine_timeframe(value: object) -> str:
    text = str(value).strip()
    minute = re.fullmatch(r"([1-9][0-9]*)m", text)
    if minute is not None:
        return minute.group(1)
    return text


def _session_from_request(request: Mapping[str, Any]) -> RC6GeneratedScriptSession:
    context = request["execution_context"]
    generated = request["generated_artifact"]
    source = request["source"]
    if not isinstance(context, Mapping) or not isinstance(generated, Mapping):
        raise ValueError("interactive RC6 identities are malformed")
    if not isinstance(source, str) or not source:
        raise ValueError("interactive RC6 source is required")
    version_context = generated.get("version_context")
    if not isinstance(version_context, Mapping):
        raise ValueError("generated version context is required")
    symbol = str(context["symbol"])
    currency = str(context["currency"])
    base_currency = symbol.removesuffix(currency) or symbol
    return RC6GeneratedScriptSession(
        artifact={"generated_artifact": generated, "python_code": source},
        language=RuntimeLanguageContext(
            int(version_context["pine_version"]),
            str(version_context["spec_snapshot_ref"]),
            "pine-v6",
            str(generated["target_manifest_hash"]),
            str(version_context["origin"]),
        ),
        instrument=InstrumentContext(
            ticker=symbol,
            tickerid=str(context["instrument_id"]),
            prefix=str(context["exchange"]).upper(),
            currency=currency,
            basecurrency=base_currency,
            timezone=str(context["timezone"]),
            instrument_type="crypto",
            mintick=float(context["mintick"]),
        ),
        timeframe=TimeframeContext.parse(_pine_timeframe(context["timeframe"])),
        identity=IntentReplayIdentity(
            run_id=str(context["run_id"]),
            strategy_id=str(context["strategy_id"]),
            stack_id=str(context["stack_manifest_hash"]),
            semantic_profile=str(context["semantic_profile"]),
            series_id=str(context["series_id"]),
            instrument_id=str(context["instrument_id"]),
            timeframe=str(context["timeframe"]),
        ),
        producer_commit=str(context["producer_commits"]["backtest_engine"]),
    )


def _strategy_values(projection: Mapping[str, Any]) -> dict[str, object]:
    validate_payload("openpine.broker_projection.v1", projection)
    if not verify_content_hash(projection, schema_id="openpine.broker_projection.v1"):
        raise ValueError("broker projection content hash is invalid")
    position = projection["position"]
    if not isinstance(position, Mapping):
        raise ValueError("broker position projection is invalid")
    direction = str(position["direction"])
    qty = float(position["qty"])
    signed_qty = -qty if direction == "SHORT" else qty
    if direction == "FLAT":
        signed_qty = 0.0
    avg_price = position.get("avg_price")
    entry_name = position.get("entry_name")
    return {
        "strategy.position_size": signed_qty,
        "strategy.position_avg_price": na if avg_price is None else float(avg_price),
        "strategy.position_entry_name": na if entry_name is None else str(entry_name),
    }


def _bar_values(raw_bar: Mapping[str, Any]) -> BarValues:
    validate_payload("openpine.marketdata.bar.v2", raw_bar)
    if not verify_content_hash(raw_bar, schema_id="openpine.marketdata.bar.v2"):
        raise ValueError("bar content hash is invalid")
    return BarValues(
        open=float(raw_bar["open"]),
        high=float(raw_bar["high"]),
        low=float(raw_bar["low"]),
        close=float(raw_bar["close"]),
        volume=float(raw_bar["volume"]),
        time=int(raw_bar["open_time_utc_ms"]),
        time_close=int(raw_bar["close_time_utc_ms"]),
    )


def run_interactive(request: Mapping[str, Any], protocol: Any) -> int:
    """Run the protocol-v2 transport with an RC6 generated-script session."""

    generated = request["generated_artifact"]
    context = request["execution_context"]
    if not isinstance(generated, Mapping) or not isinstance(context, Mapping):
        raise ValueError("interactive RC6 request identity is malformed")
    session = _session_from_request(request)
    hello = protocol.append(
        "HELLO",
        {
            "worker_id": context["session_id"],
            "protocol_version": "2.3.0",
            "capabilities": ["closed_bar", "checkpoint_v1"],
        },
        0,
    )
    json.dump(hello, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()
    loaded = False
    initialized = False
    for line in sys.stdin:
        if len(line) > 1_000_000:
            raise ValueError("interactive message exceeds size limit")
        message = json.loads(line)
        protocol.accept(message)
        kind = message["kind"]
        body = message["body"]
        if kind == "LOAD_ARTIFACT":
            entrypoint = generated["entrypoint"]
            expected = {
                "artifact_hash": generated["content_hash"],
                "module_hash": generated["emitted_module_hash"],
                "entrypoint_module": entrypoint["module"],
                "entrypoint_class": entrypoint["class"],
            }
            if body != expected:
                raise ValueError("loaded artifact identity mismatch")
            loaded = True
            continue
        if kind == "INIT_RUN":
            if not loaded:
                raise ValueError("artifact must be loaded before run initialization")
            if (
                body["execution_context"] != context
                or body["execution_context_hash"] != context["content_hash"]
                or body["run_id"] != context["run_id"]
            ):
                raise ValueError("run initialization identity mismatch")
            initialized = True
            continue
        if kind == "FINALIZE":
            return 0
        if kind == "ABORT":
            return 2
        if not initialized:
            raise ValueError("run must be initialized before bar execution")
        if kind in {"BROKER_EVENT_BATCH", "BAR_COMMIT"}:
            continue
        if kind == "RECALC_REQUEST":
            raise ValueError("RC6 recalculation requires a fresh broker-bound transaction")
        if kind != "BAR_BEGIN":
            raise ValueError("unsupported interactive message kind")
        raw_bar = body["bar"]
        projection = body["broker_projection"]
        if not isinstance(raw_bar, Mapping) or not isinstance(projection, Mapping):
            raise ValueError("interactive bar or broker projection is malformed")
        values = _bar_values(raw_bar)
        if body["bar_hash"] != raw_bar["bar_content_hash"]:
            raise ValueError("bar identity mismatch")
        execution = session.execute_bar(
            values,
            bar_index=int(body["bar_index"]),
            last_bar_index=int(body["bar_index"]),
            tick_index=int(body["recalc_iteration"]),
            strategy_values=_strategy_values(projection),
        )
        intents = [dict(intent) for intent in execution.intents]
        response = protocol.append(
            "INTENT_BATCH",
            {
                "run_id": context["run_id"],
                "bar_index": int(body["bar_index"]),
                "recalc_iteration": int(body["recalc_iteration"]),
                "intent_batch_hash": aggregate_batch_hash(
                    intents,
                    batch_kind="INTENT_BATCH",
                    item_schema_id="openpine.intent.v2",
                ),
                "intents": intents,
            },
            int(values.time),
        )
        json.dump(response, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()
    return 0


__all__ = ["RC6BarExecution", "RC6GeneratedScriptSession", "run_interactive"]
