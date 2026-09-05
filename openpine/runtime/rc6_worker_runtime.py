"""RC6 generated-script session bound to backtest-engine intents."""

from __future__ import annotations

import ast
import dataclasses
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
    ExecutionEvent,
    aggregate_batch_hash,
    seal_content_hash,
    validate_payload,
    validate_worker_protocol_sequence,
    verify_content_hash,
)
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
from pinelib.runtime.session import CallbackResult

from openpine.runtime.rc6_lifecycle import ExecutionCursor
from openpine.runtime.rc6_marketdata import RC6BarAdmission, decode_canonical_bar
from openpine.runtime.rc6_config import resolve_engine_config
from openpine.runtime.inputs import resolve_inputs, input_evidence


WORKER_STDIN_LIMIT_BYTES = 10_000_000

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
        self._sequence = 0
        self._last: dict[str, Any] | None = None

    @property
    def last_id(self) -> str | None:
        return None if self._last is None else self._last["message_id"]

    def _transition(self, kind: str) -> None:
        if self._last is None and kind != "HELLO":
            raise ValueError("worker protocol must start with HELLO")
        if self._last is not None and kind not in _PROTOCOL_AFTER[self._last["kind"]]:
            raise ValueError("invalid worker protocol transition")

    def _remember(self, payload: dict[str, Any]) -> None:
        self._last = payload
        self._sequence += 1
        if payload["kind"] not in {
            "BAR_BEGIN",
            "INTENT_BATCH",
            "BROKER_EVENT_BATCH",
            "RECALC_REQUEST",
            "RECALC_RESULT",
            "BAR_COMMIT",
        }:
            self.messages.append(payload)

    def append(
        self, kind: str, body: Mapping[str, Any], created_at_utc_ms: int
    ) -> dict[str, Any]:
        self._transition(kind)
        component = _PROTOCOL_COMPONENT[kind]
        version, commit = self.identities[component]
        sequence = self._sequence
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
        if kind in {"FINALIZE", "ABORT"} and self._sequence == len(self.messages):
            validate_worker_protocol_sequence([*self.messages, payload])
        self._remember(payload)
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
            "sequence": self._sequence,
            "correlation_id": self.context["run_id"],
            "causation_id": self.last_id,
            "sender_role": role,
        }
        if any(accepted.get(field) != value for field, value in expected.items()):
            raise ValueError("worker protocol identity mismatch")
        if kind in {"FINALIZE", "ABORT"} and self._sequence == len(self.messages):
            validate_worker_protocol_sequence([*self.messages, accepted])
        self._remember(accepted)
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
        engine_config: BacktestConfig | None = None,
        params: Mapping[str, object] | None = None,
        expected_input_values_hash: str | None = None,
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
        self.inputs = resolve_inputs(source, params, envelope=envelope)
        if expected_input_values_hash is not None and expected_input_values_hash != self.inputs.values_hash:
            raise ValueError("applied input values differ from the admitted parameter hash")
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
            inputs=self.inputs,
            instrument=instrument,
            timeframe=timeframe,
        )
        self.session.commit_full_identity = False
        if not isinstance(identity, IntentReplayIdentity):
            raise TypeError("identity must be IntentReplayIdentity")
        self.identity = identity
        self.producer_commit = producer_commit
        self.intent_config = engine_config if engine_config is not None else BacktestConfig(
            symbol=instrument.ticker,
            timeframe=identity.timeframe,
            start_time=0,
            end_time=0,
            default_qty_value=default_qty_value,
        )
        self._intent_sequence = 0
        self.execution_cursor = ExecutionCursor()

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
        broker_equity: object | None = None,
        execution_event: ExecutionEvent | None = None,
    ) -> RC6BarExecution:
        bar_time = values.time
        if type(bar_time) is not int or bar_time < 0:
            raise ValueError("bar time must be a nonnegative integer")
        if execution_event is not None:
            self.execution_cursor.validate(execution_event, values)
            if (bar_index, last_bar_index) != (execution_event.bar_index, execution_event.last_bar_index):
                raise ValueError("callback coordinates differ from execution event")
            realtime, final_tick, tick_index = (
                execution_event.realtime, execution_event.final_tick, execution_event.tick_index
            )
        handler = DelegatedStrategyIntentHandler(
            identity=self.identity,
            producer_commit=self.producer_commit,
            bar_open_time_utc_ms={bar_index: bar_time},
            config=self.intent_config,
            recalc_iteration=None if execution_event is None else execution_event.recalc_iteration,
            bar_close={bar_index: values.close},
            bar_equity={} if broker_equity is None else {bar_index: broker_equity},
        )
        self.session.delegated_dispatcher = (
            build_delegated_strategy_dispatcher(
                handler,
                strategy_values=strategy_values,
            )
        )
        transaction = self.session.begin(
            CallbackFrame(
                execution_event.phase if execution_event is not None else ("REALTIME_EVAL" if realtime else "HISTORICAL_EVAL"),
                self.session.sequence + 1,
                realtime=realtime,
                final_tick=final_tick,
                bar_index=bar_index,
                tick_index=tick_index,
                is_last_bar=bar_index == last_bar_index,
                is_last_confirmed_history=(
                    execution_event.is_last_confirmed_history if execution_event is not None
                    else not realtime and bar_index == last_bar_index
                ),
                last_bar_index=last_bar_index,
                defer_bar_commit=execution_event is not None,
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
        if execution_event is not None:
            self.execution_cursor.accept(execution_event)
        return RC6BarExecution(committed=committed, intents=intents)


    def execute_callback(
        self, values: BarValues, event: ExecutionEvent, *,
        strategy_values: Mapping[str, object], broker_equity: object | None = None,
    ) -> RC6BarExecution:
        return self.execute_bar(
            values, bar_index=event.bar_index, last_bar_index=event.last_bar_index,
            strategy_values=strategy_values, broker_equity=broker_equity,
            execution_event=event,
        )

    def finalize_bar(self, bar_index: int) -> CallbackResult:
        self.execution_cursor.require_commit(bar_index)
        result = self.session.finalize_bar(bar_index)
        self.execution_cursor.finish(bar_index)
        return result


def _pine_timeframe(value: object) -> str:
    text = str(value).strip()
    minute = re.fullmatch(r"([1-9][0-9]*)m", text)
    if minute is not None:
        return minute.group(1)
    return text


def _session_from_request(
    request: Mapping[str, Any], *, engine_config: BacktestConfig | None = None,
) -> RC6GeneratedScriptSession:
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
    if engine_config is None and "engine_config" in request:
        engine_config = resolve_engine_config(request["engine_config"], context)
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
        engine_config=engine_config,
        params=request.get("params"),
        expected_input_values_hash=request.get("input_values_hash"),
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


def _bar_values(
    raw_bar: Mapping[str, Any], *, context: Mapping[str, Any] | None = None,
) -> BarValues:
    bar = decode_canonical_bar(raw_bar, context=context)
    return BarValues(
        open=bar.open, high=bar.high, low=bar.low, close=bar.close,
        volume=bar.volume, time=bar.time, time_close=bar.time_close,
    )


class RC6InteractiveCallbacks:
    """Validate broker-bound callbacks and publish history only at BAR_COMMIT."""

    def __init__(self, session: RC6GeneratedScriptSession, context: Mapping[str, Any]) -> None:
        self.session, self.context = session, context
        self.current_bar: Mapping[str, Any] | None = None
        self.last_broker_message: Mapping[str, Any] | None = None
        self.last_commit: Mapping[str, Any] | None = None

    def process(self, message: Mapping[str, Any], protocol: RC6WorkerProtocol) -> list[dict[str, Any]]:
        kind, body = message["kind"], message["body"]
        if body.get("run_id") != self.context["run_id"]:
            raise ValueError("callback run identity mismatch")
        if kind == "BROKER_EVENT_BATCH":
            expected_hash = aggregate_batch_hash(body["broker_events"], batch_kind="BROKER_EVENT_BATCH",
                                                  item_schema_id="openpine.broker.v2")
            if body["broker_event_batch_hash"] != expected_hash:
                raise ValueError("broker event batch hash mismatch")
            self.last_broker_message = message
            return []
        if kind == "BAR_COMMIT":
            last = self.session.execution_cursor.last
            if (last is None or body["bar_index"] != last.bar_index
                    or body["recalc_iteration"] != last.recalc_iteration):
                raise ValueError("bar commit callback coordinates mismatch")
            self.session.finalize_bar(body["bar_index"])
            self.last_commit = message
            self.current_bar = self.last_broker_message = None
            return []
        if kind not in {"BAR_BEGIN", "RECALC_REQUEST"}:
            raise ValueError("unsupported interactive callback")
        event = ExecutionEvent.from_dict(body.get("execution_event"))
        if (event.bar_index != body["bar_index"]
                or event.recalc_iteration != body["recalc_iteration"]):
            raise ValueError("execution event callback coordinates mismatch")
        if kind == "BAR_BEGIN":
            if self.current_bar is not None:
                raise ValueError("previous bar has not been committed")
            raw_bar = body["bar"]
            if body["bar_hash"] != raw_bar["bar_content_hash"]:
                raise ValueError("bar identity mismatch")
        else:
            raw_bar = self.current_bar
            cause = self.last_broker_message
            if raw_bar is None or cause is None or body["cause_sequence"] != cause["sequence"]:
                raise ValueError("recalculation has no matching causal broker batch")
            if (cause["body"]["bar_index"] != event.bar_index
                    or cause["body"]["recalc_iteration"] + 1 != event.recalc_iteration):
                raise ValueError("recalculation broker coordinates mismatch")
            if body["broker_projection_hash"] != body["broker_projection"]["content_hash"]:
                raise ValueError("recalculation projection hash mismatch")
        projection = body["broker_projection"]
        expected = {key: self.context[key] for key in ("run_id", "series_id", "instrument_id")}
        expected.update(stack_id=self.context["stack_manifest_hash"], producer="backtest_engine",
                        bar_open_time_utc_ms=event.bar_open_time_utc_ms,
                        producer_commit=self.context["producer_commits"]["backtest_engine"],
                        bar_index=event.bar_index, recalc_iteration=event.recalc_iteration)
        mismatched = [key for key, value in expected.items() if projection.get(key) != value]
        if mismatched:
            raise ValueError(f"broker projection execution identity mismatch: {mismatched}")
        values = _bar_values(raw_bar, context=self.context)
        execution = self.session.execute_callback(values, event,
            strategy_values=_strategy_values(projection), broker_equity=projection["equity"])
        self.current_bar = raw_bar
        intents = [dict(intent) for intent in execution.intents]
        batch_hash = aggregate_batch_hash(intents, batch_kind="INTENT_BATCH", item_schema_id="openpine.intent.v2")
        response_body = {"run_id": self.context["run_id"], "bar_index": event.bar_index,
                        "recalc_iteration": event.recalc_iteration,
                        "intent_batch_hash": batch_hash, "intents": intents}
        responses = []
        if kind == "RECALC_REQUEST":
            responses.append(protocol.append("RECALC_RESULT", {
                "run_id": self.context["run_id"], "bar_index": event.bar_index,
                "recalc_iteration": event.recalc_iteration,
                "intent_batch_message_id": f"{self.context['session_id']}:{protocol._sequence + 1}:INTENT_BATCH",
                "intent_batch_hash": batch_hash}, event.bar_open_time_utc_ms))
        responses.append(protocol.append("INTENT_BATCH", response_body, event.bar_open_time_utc_ms))
        return responses

    def finalize(self, body: Mapping[str, Any]) -> None:
        commit = self.last_commit
        if commit is None or self.session.execution_cursor.open_bar is not None:
            raise ValueError("cannot finalize without a completed bar")
        expected = {
            "run_id": self.context["run_id"], "final_sequence": commit["sequence"],
            "final_state_hash": commit["body"]["state_hash"],
            "broker_projection_hash": commit["body"]["broker_projection_hash"],
            "last_commit_message_id": commit["message_id"],
            "last_committed_sequence": commit["sequence"],
        }
        if dict(body) != expected:
            raise ValueError("finalization does not bind the last committed bar")


def run_interactive(request: Mapping[str, Any], protocol: Any) -> int:
    """Execute causal callback events using the same deferred session as bulk."""
    generated, context = request["generated_artifact"], request["execution_context"]
    session = _session_from_request(request)
    driver = RC6InteractiveCallbacks(session, context)
    hello = protocol.append("HELLO", {"worker_id": context["session_id"],
        "protocol_version": "2.3.0", "capabilities": ["closed_bar", "checkpoint_v1"]}, 0)
    print(json.dumps(hello), flush=True)
    loaded = initialized = False
    for line in sys.stdin:
        if len(line) > WORKER_STDIN_LIMIT_BYTES:
            raise ValueError("interactive message exceeds size limit")
        message = protocol.accept(json.loads(line))
        kind, body = message["kind"], message["body"]
        if kind == "LOAD_ARTIFACT":
            entrypoint = generated["entrypoint"]
            if body != {"artifact_hash": generated["content_hash"], "module_hash": generated["emitted_module_hash"],
                        "entrypoint_module": entrypoint["module"], "entrypoint_class": entrypoint["class"]}:
                raise ValueError("loaded artifact identity mismatch")
            loaded = True
        elif kind == "INIT_RUN":
            if (not loaded or body["execution_context"] != context
                    or body["execution_context_hash"] != context["content_hash"] or body["run_id"] != context["run_id"]):
                raise ValueError("run initialization identity mismatch")
            initialized = True
        elif kind == "ABORT":
            return 2
        elif kind == "FINALIZE":
            if not initialized or session.execution_cursor.open_bar is not None:
                raise ValueError("cannot finalize a provisional or uninitialized run")
            driver.finalize(body)
            return 0
        else:
            if not initialized:
                raise ValueError("run must be initialized before execution")
            for response in driver.process(message, protocol):
                print(json.dumps(response), flush=True)
    raise ValueError("interactive input ended without FINALIZE")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _engine_bar(envelope: Mapping[str, Any]) -> Any:
    return decode_canonical_bar(envelope)


def run_bulk(request: Mapping[str, Any], protocol: Any) -> int:
    """Run the engine+generated loop inside the sandbox. Host sees no per-bar IPC."""

    from backtest_engine import BacktestCallbacks, BacktestEngine
    from backtest_engine.core.intent_replay import (
        admit_sealed_intent_tape,
        apply_live_intents_for_bar,
    )

    generated = request["generated_artifact"]
    context = request["execution_context"]
    engine_config = request.get("engine_config")
    if not isinstance(generated, Mapping) or not isinstance(context, Mapping):
        raise ValueError("bulk RC6 request identity is malformed")
    if not isinstance(engine_config, Mapping):
        raise ValueError("bulk engine config is required")
    config = resolve_engine_config(engine_config, context)
    session = _session_from_request(request, engine_config=config)
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
    engine_bars: list[Any] = []
    bar_admission = RC6BarAdmission(context)
    received_last_batch = False
    for line in sys.stdin:
        if len(line) > WORKER_STDIN_LIMIT_BYTES:
            raise ValueError("interactive message exceeds size limit")
        message = json.loads(line)
        kind = message.get("kind") if isinstance(message, dict) else None
        if kind == "BULK_BARS":
            if not initialized:
                raise ValueError("run must be initialized before bulk bars")
            batch = message.get("bars")
            if not isinstance(batch, list):
                raise ValueError("bulk bars payload is invalid")
            for item in batch:
                admitted_bar = bar_admission.accept(item)
                if admitted_bar is not None:
                    engine_bars.append(admitted_bar)
            if message.get("last") is True:
                received_last_batch = True
                break
            continue
        protocol.accept(message)
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
        if kind == "ABORT":
            return 2
        raise ValueError("unsupported bulk handshake message kind")
    if not received_last_batch:
        raise ValueError("bulk bar stream ended before its final batch")
    if not initialized or not engine_bars:
        raise ValueError("bulk backtest did not receive bars")

    completed_bars = 0
    tape_events: list[dict[str, Any]] = []

    def _strategy_values_from_state(state: Any) -> dict[str, object]:
        signed = float(getattr(state, "position_size", 0.0) or 0.0)
        avg = getattr(state, "position_avg_price", None)
        opens = getattr(state, "_open_trades_ref", None) or []
        entry = str(opens[0].entry_id) if opens else None
        return {
            "strategy.position_size": signed,
            "strategy.position_avg_price": na if avg is None else float(avg),
            "strategy.position_entry_name": na if entry is None else entry,
        }

    class _BulkStrategy:
        required_runtime_capabilities: tuple[str, ...] = ()

        def __init__(self, params: dict[str, Any], runtime: Any, ctx: Any) -> None:
            del runtime
            if params != dict(session.inputs.values):
                raise ValueError("bulk broker parameters differ from applied Pine inputs")
            self.ctx = ctx

        def run_callback(self, bar: Any, event: ExecutionEvent) -> None:
            bar_index = event.bar_index
            values = BarValues(
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume or 0),
                time=int(bar.time),
                time_close=int(bar.time_close or bar.time),
            )
            execution = session.execute_callback(
                values, event,
                strategy_values=_strategy_values_from_state(self.ctx.state),
                broker_equity=self.ctx.state.equity,
            )
            batch = [dict(intent) for intent in execution.intents]
            tape_events.extend(batch)
            if not batch:
                return
            origin = int(batch[0]["sequence"])
            current = admit_sealed_intent_tape(batch, sequence_origin=origin)
            apply_live_intents_for_bar(
                self.ctx,
                current,
                bar_index,
                bar_open_time_utc_ms=int(bar.time),
            )

        def export_state(self) -> dict[str, Any]:
            return {"bulk_worker": True}

        def restore_state(self, state: Any) -> None:
            del state

    from openpine.runtime.progress import ProgressReporter
    total = len(engine_bars)

    def send_progress(done: int, count: int) -> None:
        json.dump({"kind": "BULK_PROGRESS", "bars_done": done, "bars_total": count}, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()

    progress = ProgressReporter(send_progress, max_total=total)
    progress.report(0, total)

    def on_bar_end(_bar: Any, index: int, _state: Any) -> None:
        nonlocal completed_bars
        session.finalize_bar(index)
        completed_bars += 1
        progress.report(completed_bars, total)

    result = BacktestEngine(config).run(
        _BulkStrategy,
        params=dict(session.inputs.values),
        bars=engine_bars,
        callbacks=BacktestCallbacks(on_bar_end=on_bar_end),
    )
    if getattr(result, "status", None) != "completed":
        raise ValueError(
            f"bulk engine did not complete: {getattr(result, 'status', None)!r}; "
            f"{getattr(result, 'errors', [])!r}"
        )
    progress.report(completed_bars, total, force=True)
    raw = {
        "status": getattr(result, "status", "completed"),
        "bars_processed": completed_bars,
        "initial_capital": getattr(result, "initial_capital", None),
        "final_equity": getattr(result, "final_equity", None),
        "net_profit": getattr(result, "net_profit", None),
        "net_profit_percent": getattr(result, "net_profit_percent", None),
        "gross_profit": getattr(result, "gross_profit", None),
        "gross_loss": getattr(result, "gross_loss", None),
        "profit_factor": getattr(result, "profit_factor", None),
        "max_drawdown": getattr(result, "max_drawdown", None),
        "max_drawdown_percent": getattr(result, "max_drawdown_percent", None),
        "sharpe_ratio": getattr(result, "sharpe_ratio", None),
        "sortino_ratio": getattr(result, "sortino_ratio", None),
        "win_rate": getattr(result, "win_rate", None),
        "total_trades": getattr(result, "total_trades", 0),
        "winning_trades": getattr(result, "winning_trades", 0),
        "losing_trades": getattr(result, "losing_trades", 0),
        "avg_trade": getattr(result, "avg_trade", None),
        "score_ledger_hash": result.score_ledger_hash,
        "trades": _jsonable(list(getattr(result, "trades", None) or [])),
        "closed_trades": _jsonable(list(getattr(result, "closed_trades", None) or [])),
        "open_trades": _jsonable(list(getattr(result, "open_trades", None) or [])),
        "equity_curve": _jsonable(list(getattr(result, "equity_curve", None) or [])),
        "events": _jsonable(list(getattr(result, "events", None) or [])),
        "warnings": _jsonable(list(getattr(result, "warnings", None) or [])),
        "errors": _jsonable(list(getattr(result, "errors", None) or [])),
        "effective_config_hash": config.effective_config_hash,
        "config_snapshot": _jsonable(result.config_snapshot),
        **input_evidence(session.inputs),
    }
    payload = {
        "kind": "BULK_RESULT",
        "bars_received": bar_admission.received,
        "bars_excluded_open": bar_admission.excluded_open,
        "bars_processed": completed_bars,
        "intent_tape": tape_events,
        "score_ledger_hash": result.score_ledger_hash,
        "raw_result": raw,
    }
    from openpine.runtime.bulk_result import encode_result, result_identity
    for frame in encode_result(payload, identity=result_identity(context, raw)):
        json.dump(frame, sys.stdout, separators=(",", ":"), allow_nan=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
    for line in sys.stdin:
        if len(line) > WORKER_STDIN_LIMIT_BYTES:
            raise ValueError("interactive message exceeds size limit")
        message = json.loads(line)
        kind = message.get("kind") if isinstance(message, dict) else None
        if kind == "ABORT":
            return 2
        if kind == "FINALIZE":
            return 0
    return 0


__all__ = ["RC6BarExecution", "RC6GeneratedScriptSession", "run_bulk", "run_interactive"]
