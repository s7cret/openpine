"""Durable execution cursor/outbox in the existing job owner's transaction."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any, Literal, TYPE_CHECKING

from openpine_contracts import canonical_dumps, validate_payload, verify_content_hash

from openpine.jobs.transactional_store import JobV1Error, JobV1Store
from openpine.runtime.job_checkpoint import (
    MAX_BYTES,
    decode_job_checkpoint,
    encode_job_checkpoint,
    prepare_job_checkpoint,
)
from openpine.runtime.worker_protocol import WorkerProtocolTranscript

if TYPE_CHECKING:
    from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession


class JobExecutionStore(JobV1Store):
    """One SQLite WAL/FULL owner for jobs, generations, cuts and delivery acks.

    Frames beyond the last complete cut remain private. Recovery discards that
    provisional suffix, retaining the last checkpoint and every durable ack.
    """

    def _initialize_schema(self) -> None:
        super()._initialize_schema()
        with self._transaction():
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_runs (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id), run_id TEXT NOT NULL,
                    context TEXT NOT NULL, worker_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, lease_owner TEXT NOT NULL,
                    checkpoint BLOB, committed_sequence INTEGER NOT NULL DEFAULT -1,
                    last_ack INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY(job_id, run_id)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_frames (
                    job_id TEXT NOT NULL, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    generation INTEGER NOT NULL, message BLOB NOT NULL,
                    PRIMARY KEY(job_id, run_id, sequence),
                    FOREIGN KEY(job_id, run_id) REFERENCES execution_runs(job_id, run_id)
                )
            """)

    def _run(self, job_id: str, run_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM execution_runs WHERE job_id = ? AND run_id = ?", (job_id, run_id)
        ).fetchone()
        if row is None:
            raise JobV1Error("execution run does not exist")
        return dict(row)

    def _fence(self, job_id: str, run_id: str, generation: int, *, now_ms: int) -> dict[str, Any]:
        if type(generation) is not int:
            raise JobV1Error("worker generation is invalid")
        job, run = self._get_in_transaction(job_id), self._run(job_id, run_id)
        if job["state"] != "RUNNING" or job["run_id"] != run_id:
            raise JobV1Error("execution publication requires the active RUNNING run")
        self._require_live_lease(job, lease_owner=run["lease_owner"], now_ms=now_ms)
        if run["generation"] != generation:
            raise JobV1Error("stale worker generation")
        return run

    def claim_execution(
        self,
        job_id: str,
        *,
        context: Mapping[str, Any],
        lease_owner: str,
        worker_id: str,
        now_ms: int,
        mode: Literal["fresh", "resume", "new_run"] = "fresh",
        resume_session: RC6GeneratedScriptSession | None = None,
    ) -> int:
        """A transport retry keeps its token; worker replacement advances it.

        A job retry resumes the same run only when explicitly requested. New run
        means a different run_id and starts independent sequences/outbox state.
        """
        WorkerProtocolTranscript(context)
        if (
            type(worker_id) is not str
            or not worker_id
            or type(lease_owner) is not str
            or not lease_owner
            or mode not in {"fresh", "resume", "new_run"}
        ):
            raise JobV1Error("execution claim identity/mode is invalid")
        run_id = str(context["run_id"])
        admitted = None
        if mode == "resume":
            if resume_session is None:
                raise JobV1Error("resume requires the selected generated owner")
            with self._lock:
                admitted = self._run(job_id, run_id)["checkpoint"]
            if admitted is None:
                raise JobV1Error("execution has no committed checkpoint")
            prepare_job_checkpoint(
                bytes(admitted), job_id=job_id, context=context, session=resume_session
            )
        with self._transaction():
            job = self._get_in_transaction(job_id)
            if job["state"] != "RUNNING":
                raise JobV1Error("execution claim requires a RUNNING job")
            self._require_live_lease(job, lease_owner=lease_owner, now_ms=now_ms)
            row = self._conn.execute(
                "SELECT * FROM execution_runs WHERE job_id = ? AND run_id = ?", (job_id, run_id)
            ).fetchone()
            if row is not None:
                run = dict(row)
                if json.loads(run["context"]) != dict(context) or job["run_id"] != run_id:
                    raise JobV1Error("execution context or active run mismatch")
                if run["worker_id"] == worker_id and run["lease_owner"] == lease_owner:
                    return int(run["generation"])
                if mode != "resume" or run["checkpoint"] != admitted:
                    raise JobV1Error("worker replacement requires the same admitted checkpoint")
            elif mode == "resume" or (job["run_id"] is not None and mode != "new_run"):
                raise JobV1Error("new run requires an explicit new_run claim")
            generation = (
                int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(generation), 0) FROM execution_runs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()[0]
                )
                + 1
            )
            if row is None:
                self._conn.execute(
                    "INSERT INTO execution_runs(job_id, run_id, context, worker_id, generation, lease_owner) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (job_id, run_id, canonical_dumps(context), worker_id, generation, lease_owner),
                )
            else:
                self._conn.execute(
                    "UPDATE execution_runs SET generation = ?, worker_id = ?, lease_owner = ? "
                    "WHERE job_id = ? AND run_id = ?",
                    (generation, worker_id, lease_owner, job_id, run_id),
                )
                self._conn.execute(
                    "DELETE FROM execution_frames WHERE job_id = ? AND run_id = ? AND sequence > ?",
                    (job_id, run_id, row["committed_sequence"]),
                )
            old_version = int(job["version"])
            job.update(run_id=run_id, version=old_version + 1, updated_at_utc_ms=now_ms)
            self._store_job_in_transaction(job, expected_version=old_version)
            self._append_event_in_transaction(job_id, "execution_claimed", job)
            return generation

    def record_frame(
        self, job_id: str, run_id: str, generation: int, message: Mapping[str, Any], *, now_ms: int
    ) -> bool:
        validate_payload("openpine.worker.protocol.v2", message)
        if not verify_content_hash(message, schema_id="openpine.worker.protocol.v2"):
            raise JobV1Error("execution frame hash mismatch")
        encoded = canonical_dumps(message).encode("utf-8")
        sequence = message["sequence"]
        with self._transaction():
            run = self._fence(job_id, run_id, generation, now_ms=now_ms)
            context = json.loads(run["context"])
            expected = {
                "run_id": run_id,
                "correlation_id": run_id,
                "session_id": context["session_id"],
                "stack_id": context["stack_manifest_hash"],
            }
            if any(message[key] != value for key, value in expected.items()):
                raise JobV1Error("execution frame identity mismatch")
            existing = self._conn.execute(
                "SELECT message FROM execution_frames WHERE job_id = ? AND run_id = ? AND sequence = ?",
                (job_id, run_id, sequence),
            ).fetchone()
            if existing is not None:
                if bytes(existing[0]) != encoded:
                    raise JobV1Error("conflicting transport repeat")
                return False
            previous = self._conn.execute(
                "SELECT sequence, message FROM execution_frames WHERE job_id = ? AND run_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (job_id, run_id),
            ).fetchone()
            expected_sequence = 0 if previous is None else int(previous[0]) + 1
            previous_id = None if previous is None else json.loads(previous[1])["message_id"]
            if sequence != expected_sequence or message["causation_id"] != previous_id:
                raise JobV1Error("execution frame sequence/causation gap")
            total = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(message)), 0) FROM execution_frames WHERE job_id = ? AND run_id = ?",
                (job_id, run_id),
            ).fetchone()[0]
            if int(total) + len(encoded) > MAX_BYTES:
                raise JobV1Error("durable transcript exceeds its byte budget")
            self._conn.execute(
                "INSERT INTO execution_frames VALUES (?, ?, ?, ?, ?)",
                (job_id, run_id, sequence, generation, encoded),
            )
            return True

    def import_checkpoint(
        self,
        job_id: str,
        wire: bytes,
        *,
        context: Mapping[str, Any],
        session: RC6GeneratedScriptSession,
        lease_owner: str,
        worker_id: str,
        now_ms: int,
    ) -> int:
        """Restore portable bytes into a new job execution ledger atomically.

        Existing ledgers require normal resume; importing cannot roll back their
        checkpoint, generation or acknowledged effects. Immutable artifact bytes
        are carried in the envelope, so producer artifact paths are not read.
        """
        prepared = prepare_job_checkpoint(wire, job_id=job_id, context=context, session=session)
        if type(worker_id) is not str or not worker_id:
            raise JobV1Error("execution worker identity is invalid")
        payload = prepared.payload
        run_id = str(context["run_id"])
        with self._transaction():
            job = self._get_in_transaction(job_id)
            if job["state"] != "RUNNING" or job["run_id"] is not None:
                raise JobV1Error("checkpoint import requires a new RUNNING execution ledger")
            self._require_live_lease(job, lease_owner=lease_owner, now_ms=now_ms)
            if self._conn.execute(
                "SELECT 1 FROM execution_runs WHERE job_id = ?", (job_id,)
            ).fetchone():
                raise JobV1Error("checkpoint import cannot replace an existing execution ledger")
            generation = payload["worker_generation"] + 1
            self._conn.execute(
                "INSERT INTO execution_runs(job_id, run_id, context, worker_id, generation, lease_owner, "
                "checkpoint, committed_sequence, last_ack) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    run_id,
                    canonical_dumps(context),
                    worker_id,
                    generation,
                    lease_owner,
                    wire,
                    payload["committed_sequence"],
                    payload["last_acknowledged_frame"],
                ),
            )
            self._conn.executemany(
                "INSERT INTO execution_frames VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        run_id,
                        message["sequence"],
                        payload["worker_generation"],
                        canonical_dumps(message).encode("utf-8"),
                    )
                    for message in payload["protocol_messages"]
                ],
            )
            old_version = int(job["version"])
            job.update(run_id=run_id, version=old_version + 1, updated_at_utc_ms=now_ms)
            self._store_job_in_transaction(job, expected_version=old_version)
            self._append_event_in_transaction(job_id, "execution_restored", job)
            return int(generation)

    def frames(
        self, job_id: str, run_id: str, *, committed_only: bool = True
    ) -> list[dict[str, Any]]:
        with self._lock:
            run = self._run(job_id, run_id)
            limit = run["committed_sequence"] if committed_only else 2**63 - 1
            return [
                json.loads(row[0])
                for row in self._conn.execute(
                    "SELECT message FROM execution_frames WHERE job_id = ? AND run_id = ? "
                    "AND sequence <= ? ORDER BY sequence",
                    (job_id, run_id, limit),
                ).fetchall()
            ]

    def publish_checkpoint(
        self,
        job_id: str,
        run_id: str,
        generation: int,
        wire: bytes,
        *,
        context: Mapping[str, Any],
        session: RC6GeneratedScriptSession,
        now_ms: int,
    ) -> None:
        candidate = prepare_job_checkpoint(
            wire, job_id=job_id, context=context, session=session, worker_generation=generation
        )
        with self._transaction():
            run = self._fence(job_id, run_id, generation, now_ms=now_ms)
            payload = candidate.payload
            if (
                json.loads(run["context"]) != dict(context)
                or payload["last_acknowledged_frame"] != run["last_ack"]
                or payload["committed_sequence"] < run["committed_sequence"]
            ):
                raise JobV1Error("checkpoint context/ack/boundary conflict")
            if payload["protocol_messages"] != self.frames(job_id, run_id, committed_only=False):
                raise JobV1Error("checkpoint does not bind the complete durable transcript")
            if (
                payload["committed_sequence"] == run["committed_sequence"]
                and run["checkpoint"] is not None
            ):
                previous = decode_job_checkpoint(bytes(run["checkpoint"]))
                comparable = {"worker_generation", "last_acknowledged_frame", "content_hash"}
                if {k: v for k, v in previous.items() if k not in comparable} != {
                    k: v for k, v in payload.items() if k not in comparable
                }:
                    raise JobV1Error("conflicting checkpoint at the same boundary")
            self._conn.execute(
                "UPDATE execution_runs SET checkpoint = ?, committed_sequence = ? "
                "WHERE job_id = ? AND run_id = ?",
                (wire, payload["committed_sequence"], job_id, run_id),
            )
            self._append_event_in_transaction(
                job_id, "execution_checkpoint", self._get_in_transaction(job_id)
            )

    def checkpoint(self, job_id: str, run_id: str) -> bytes:
        with self._lock:
            run = self._run(job_id, run_id)
            if run["checkpoint"] is None:
                raise JobV1Error("execution has no committed checkpoint")
            payload = decode_job_checkpoint(bytes(run["checkpoint"]))
            payload.update(
                worker_generation=run["generation"], last_acknowledged_frame=run["last_ack"]
            )
            return encode_job_checkpoint(payload)

    def delivery_checkpoint(self, binding: Any, *, now_ms: int) -> bytes:
        """Read the active owned cut and physical binding in one transaction."""
        with self._transaction():
            run = self._fence(
                binding.job_id, binding.run_id, binding.worker_generation, now_ms=now_ms
            )
            context = json.loads(run["context"])
            if (
                binding.worker_id != run["worker_id"]
                or binding.session_id != context["session_id"]
                or binding.stack_id != context["stack_manifest_hash"]
                or binding.producer_commit != context["producer_commits"]["openpine"]
            ):
                raise JobV1Error("delivery physical binding differs from its durable owner")
            return self.checkpoint(binding.job_id, binding.run_id)

    def acknowledge(
        self,
        job_id: str,
        run_id: str,
        generation: int,
        *,
        sequence: int,
        message_id: str,
        content_hash: str,
        now_ms: int,
    ) -> bool:
        if type(sequence) is not int or sequence < 0:
            raise JobV1Error("ack sequence is invalid")
        with self._transaction():
            run = self._fence(job_id, run_id, generation, now_ms=now_ms)
            if sequence > run["committed_sequence"]:
                raise JobV1Error("cannot acknowledge a provisional frame")
            row = self._conn.execute(
                "SELECT message FROM execution_frames WHERE job_id = ? AND run_id = ? AND sequence = ?",
                (job_id, run_id, sequence),
            ).fetchone()
            message = {} if row is None else json.loads(row[0])
            if (
                message.get("message_id") != message_id
                or message.get("content_hash") != content_hash
            ):
                raise JobV1Error("ack frame identity mismatch")
            if sequence <= run["last_ack"]:
                return False
            if sequence != run["last_ack"] + 1:
                raise JobV1Error("ack sequence gap")
            self._conn.execute(
                "UPDATE execution_runs SET last_ack = ? WHERE job_id = ? AND run_id = ?",
                (sequence, job_id, run_id),
            )
            return True

    def pending_frames(
        self, job_id: str, run_id: str, generation: int, *, now_ms: int
    ) -> list[dict[str, Any]]:
        with self._transaction():
            run = self._fence(job_id, run_id, generation, now_ms=now_ms)
            return self.frames(job_id, run_id)[run["last_ack"] + 1 :]
