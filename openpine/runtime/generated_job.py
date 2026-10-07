"""Committed native generated execution with the durable job protocol owner.

This is the local native path. Protected-worker capability negotiation remains
closed_bar until the isolated driver implements the complete restore transport.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import os
from pathlib import Path
import tempfile
import time
from typing import Any, TYPE_CHECKING

from backtest_engine import BacktestCallbacks, BacktestEngine, JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import Bar, BarSeries
from backtest_engine.results import BacktestResult
from openpine_contracts import aggregate_batch_hash, ExecutionEvent

from openpine.jobs.execution_store import JobExecutionStore
from openpine.runtime.generated_backtest import RC6GeneratedExecutionBackend
from openpine.runtime.job_checkpoint import MAX_BYTES, make_job_checkpoint, prepare_job_checkpoint
from openpine.runtime.worker_capabilities import WORKER_CAPABILITIES
from openpine.runtime.worker_protocol import WorkerProtocolTranscript

if TYPE_CHECKING:
    from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession


class GeneratedJobExecution:
    """Publish one supported cut, then expose its outbox for ordered delivery."""

    def delivery_sender(self, *, requested_capabilities: tuple[str, ...]) -> Any:
        """Opt into delivery after publication; legacy construction stays unchanged."""
        from openpine.runtime.worker_delivery import JobDeliverySender, binding_for

        return JobDeliverySender(
            self.store,
            binding_for(
                self.context,
                job_id=self.job_id,
                worker_id=self.worker_id,
                generation=self.generation,
            ),
            self.context,
            requested_capabilities=requested_capabilities,
            now_ms=self.clock(),
        )

    def deliver_committed(
        self,
        request: Mapping[str, Any],
        *,
        requested_capabilities: tuple[str, ...],
        admitted_manifest: Mapping[str, Any],
        timeout_s: float = 30.0,
        cgroup_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Opt into a finite protected cut exchange after durable publication."""
        from openpine.runtime.isolated_worker import execute_committed_delivery

        return execute_committed_delivery(
            self.delivery_sender(requested_capabilities=requested_capabilities),
            request,
            admitted_manifest=admitted_manifest,
            clock=self.clock,
            timeout_s=timeout_s,
            cgroup_dir=cgroup_dir,
        )

    def __init__(
        self,
        store: JobExecutionStore,
        *,
        job_id: str,
        context: Mapping[str, Any],
        generation: int,
        worker_id: str,
        session: RC6GeneratedScriptSession,
        generated_artifact: Mapping[str, Any],
        resume: bool = False,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.store, self.job_id, self.context = store, job_id, dict(context)
        self.generation, self.worker_id, self.session = generation, worker_id, session
        self.run_id = str(context["run_id"])
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.artifacts: dict[str, bytes] = {}
        self.native_bytes: bytes | None = None
        self._recalc = False
        self._event_error: Exception | None = None

        def sink(message: dict[str, Any]) -> None:
            store.record_frame(job_id, self.run_id, generation, message, now_ms=self.clock())

        if resume:
            prepared = prepare_job_checkpoint(
                store.checkpoint(job_id, self.run_id),
                job_id=job_id,
                context=context,
                session=session,
                worker_generation=generation,
            )
            self.native_bytes = prepared.native_bytes
            self.artifacts = {
                key: base64.b64decode(value, validate=True)
                for key, value in prepared.payload["artifacts"].items()
            }
            self.protocol = WorkerProtocolTranscript.from_committed_messages(
                context, prepared.payload["protocol_messages"], on_message=sink
            )
        else:
            self.protocol = WorkerProtocolTranscript(context, on_message=sink)
            self.protocol.append(
                "HELLO",
                {
                    "worker_id": worker_id,
                    "protocol_version": "2.3.0",
                    "capabilities": list(WORKER_CAPABILITIES),
                },
                created_at_utc_ms=0,
            )
            artifact = generated_artifact
            if artifact["content_hash"] != context["generated_artifact_hash"]:
                raise ResumeUnsupportedError("job generated artifact identity mismatch")
            self.protocol.append(
                "LOAD_ARTIFACT",
                {
                    "artifact_hash": artifact["content_hash"],
                    "module_hash": artifact["emitted_module_hash"],
                    "entrypoint_module": artifact["entrypoint"]["module"],
                    "entrypoint_class": artifact["entrypoint"]["class"],
                },
                created_at_utc_ms=0,
            )
            self.protocol.append(
                "INIT_RUN",
                {
                    "run_id": self.run_id,
                    "run_hash": context["content_hash"],
                    "execution_context_hash": context["content_hash"],
                    "execution_context": dict(context),
                    "semantic_profile": context["semantic_profile"],
                    "capabilities": list(WORKER_CAPABILITIES),
                },
                created_at_utc_ms=0,
            )

    def _artifact(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """Make referenced bytes durable before any committed frame is visible."""
        encoded, identity = artifact["bytes"], artifact["artifact_hash"]
        if type(encoded) is not bytes:
            raise ResumeUnsupportedError("protocol artifact bytes are missing")
        if (
            identity not in self.artifacts
            and sum(map(len, self.artifacts.values())) + len(encoded) > MAX_BYTES
        ):
            raise ResumeUnsupportedError("durable protocol artifacts exceed their byte budget")
        folder = self.store.path.parent / (self.store.path.name + ".artifacts")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (identity[7:] + ".json")
        if path.exists():
            if path.read_bytes() != encoded:
                raise ResumeUnsupportedError("protocol artifact hash collision")
        else:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
                temporary = Path(output.name)
                try:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        self.artifacts[identity] = encoded
        return {
            key: artifact[key] for key in ("schema_id", "codec", "size_bytes", "artifact_hash")
        } | {"uri": path.resolve().as_uri()}

    def _event(self, event: dict[str, Any]) -> None:
        try:
            self._process_event(event)
        except Exception as error:
            self._event_error = error
            raise

    def _process_event(self, event: dict[str, Any]) -> None:
        kind = event["kind"]
        opened = int(event["bar_open_time_utc_ms"])
        if kind == "BAR_BEGIN":
            self._recalc = False
            body = {
                key: event[key]
                for key in (
                    "run_id",
                    "bar_index",
                    "bar_open_time_utc_ms",
                    "recalc_iteration",
                    "bar_hash",
                    "bar",
                    "broker_projection",
                    "execution_event",
                )
            }
        elif kind == "RECALC_REQUEST":
            self._recalc = True
            batch = self.protocol.append(
                "BROKER_EVENT_BATCH",
                {
                    "run_id": self.run_id,
                    "bar_index": event["bar_index"],
                    "recalc_iteration": event["recalc_iteration"] - 1,
                    "broker_event_batch_hash": event["broker_event_batch_hash"],
                    "broker_events": event["broker_events"],
                },
                created_at_utc_ms=opened,
            )
            body = {
                key: event[key]
                for key in (
                    "run_id",
                    "bar_index",
                    "recalc_iteration",
                    "execution_event",
                    "broker_projection_hash",
                    "broker_projection",
                )
            } | {"cause_sequence": batch["sequence"]}
        elif kind == "BAR_COMMIT":
            body = {
                key: event[key]
                for key in (
                    "run_id",
                    "bar_index",
                    "recalc_iteration",
                    "state_hash",
                    "broker_projection_hash",
                )
            } | {
                "state_ref": self._artifact(event["state_artifact"]),
                "broker_projection_ref": self._artifact(event["broker_projection_artifact"]),
            }
        else:
            raise ResumeUnsupportedError("unsupported job protocol event")
        self.protocol.append(kind, body, created_at_utc_ms=opened)

    def _intents(self, event: ExecutionEvent, intents: list[dict[str, Any]]) -> None:
        if self._event_error is not None:
            raise self._event_error
        identity = {
            "run_id": self.run_id,
            "bar_index": event.bar_index,
            "recalc_iteration": event.recalc_iteration,
        }
        batch_hash = aggregate_batch_hash(
            intents, batch_kind="INTENT_BATCH", item_schema_id="openpine.intent.v2"
        )
        if self._recalc:
            self.protocol.append(
                "RECALC_RESULT",
                identity
                | {
                    "intent_batch_message_id": f"{self.context['session_id']}:{self.protocol.next_sequence + 1}:INTENT_BATCH",
                    "intent_batch_hash": batch_hash,
                },
                created_at_utc_ms=event.bar_open_time_utc_ms,
            )
        self.protocol.append(
            "INTENT_BATCH",
            identity
            | {
                "intent_batch_hash": batch_hash,
                "intents": intents,
            },
            created_at_utc_ms=event.bar_open_time_utc_ms,
        )

    def run(
        self,
        engine: BacktestEngine,
        bars: BarSeries | list[Bar],
        *,
        bar_envelopes: list[dict[str, Any]],
    ) -> BacktestResult:
        if engine.config.calc_on_every_tick or not engine.config.export_resume_state:
            raise ResumeUnsupportedError(
                "durable job execution requires historical committed cuts with export enabled"
            )
        tape: list[dict[str, Any]] = []
        result = engine.run(
            self.session.generated_class,
            bars=bars,
            resume_state=self.native_bytes,
            execution_backend=RC6GeneratedExecutionBackend(
                self.session, tape, on_intents=self._intents
            ),
            callbacks=BacktestCallbacks(on_protocol_callback=self._event),
            execution_context=self.context,
            bar_envelopes=bar_envelopes,
        )
        if self._event_error is not None:
            raise self._event_error
        if result.status not in {"completed", "early_stopped"} or result.resume_state is None:
            raise ResumeUnsupportedError("job execution did not reach a supported committed cut")
        native = JsonResumeStateSerializer().dumps(result.resume_state)
        try:
            from openpine.runtime.job_checkpoint import decode_job_checkpoint

            last_ack = decode_job_checkpoint(self.store.checkpoint(self.job_id, self.run_id))[
                "last_acknowledged_frame"
            ]
        except ValueError as error:
            from openpine.jobs.transactional_store import JobV1Error

            if not isinstance(error, JobV1Error):
                raise
            last_ack = -1
        wire = make_job_checkpoint(
            job_id=self.job_id,
            context=self.context,
            worker_generation=self.generation,
            native_bytes=native,
            messages=self.store.frames(self.job_id, self.run_id, committed_only=False),
            artifacts=self.artifacts,
            last_acknowledged_frame=last_ack,
        )
        self.store.publish_checkpoint(
            self.job_id,
            self.run_id,
            self.generation,
            wire,
            context=self.context,
            session=self.session,
            now_ms=self.clock(),
        )
        self.native_bytes = native
        return result
