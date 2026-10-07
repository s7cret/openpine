"""Opt-in bounded delivery adapters over the existing committed job owners.

The legacy isolated driver remains closed_bar. These adapters never choose a
generation from wire, launch workers or weaken staging/owner admission.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import asdict
import json
from typing import Any, BinaryIO, TYPE_CHECKING

from backtest_engine import JsonResumeStateSerializer
from openpine_contracts import canonical_dumps, schema_hash
from openpine_contracts.worker_delivery import (
    FRAME_BYTES,
    PAYLOAD_BYTES,
    LIMITS,
    SCHEMA_ID,
    TRANSCRIPT_BYTES,
    TRANSCRIPT_FRAMES,
    CutReceipt,
    DeliveryBinding,
    decode_frame,
    encode_frame,
    payload_frames,
    raw_hash,
    validate_delivery_sequence,
)

from openpine.runtime.job_checkpoint import decode_job_checkpoint, prepare_job_checkpoint

if TYPE_CHECKING:
    from openpine.jobs.execution_store import JobExecutionStore
    from openpine.runtime.rc6_worker_runtime import RC6InteractiveCallbacks

DELIVERY_CAPABILITIES = ("delivery_v1", "committed_job_v1")


def require_delivery(value: object) -> None:
    if not isinstance(value, (list, tuple)) or tuple(value) != DELIVERY_CAPABILITIES:
        raise ValueError("recovery requires explicit delivery_v1 and committed_job_v1 negotiation")


def binding_for(
    context: Mapping[str, Any], *, job_id: str, worker_id: str, generation: int
) -> DeliveryBinding:
    return DeliveryBinding(
        job_id,
        context["run_id"],
        context["session_id"],
        worker_id,
        generation,
        context["stack_manifest_hash"],
        context["producer_commits"]["openpine"],
    )


def cut_view(binding: DeliveryBinding, wire: bytes) -> CutReceipt:
    """Project bytes already admitted by the durable/native/Pine owner."""
    p = decode_job_checkpoint(wire)
    if (p["job_id"], p["run_id"], p["worker_generation"]) != (
        binding.job_id,
        binding.run_id,
        binding.worker_generation,
    ):
        raise ValueError("delivery cut identity/generation mismatch")
    return CutReceipt(
        binding,
        p["content_hash"],
        raw_hash(wire),
        len(wire),
        p["callback_sequence"],
        p["intent_sequence"],
        tuple(canonical_dumps(m).encode() for m in p["protocol_messages"]),
        p["last_acknowledged_frame"],
    )


def _template(binding: DeliveryBinding, context: Mapping[str, Any]) -> dict[str, Any]:
    version = next(
        row["version"] for row in context["wheel_identities"] if row["name"] == "openpine"
    )
    return {
        **asdict(binding),
        "schema_id": SCHEMA_ID,
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": version.replace("rc", "-rc."),
        "created_at_utc_ms": 0,
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "sender_role": "parent",
    }


def _frame(
    template: Mapping[str, Any], kind: str, body: dict[str, Any], *, role: str = "parent"
) -> bytes:
    identity = raw_hash(canonical_dumps({"kind": kind, "body": body, "role": role}).encode())
    return encode_frame(
        {**template, "kind": kind, "body": body, "sender_role": role, "delivery_id": identity}
    )


def _hello(template: Mapping[str, Any], cut: CutReceipt) -> bytes:
    return _frame(
        template,
        "HELLO",
        {
            "capabilities": list(DELIVERY_CAPABILITIES),
            "inner_schema_hash": schema_hash("openpine.worker.protocol.v2"),
            "delivery_schema_hash": schema_hash(SCHEMA_ID),
            "checkpoint_hash": cut.checkpoint_hash,
            "limits": LIMITS,
        },
        role="worker",
    )


def _control(template: Mapping[str, Any], kind: str, cut: CutReceipt) -> bytes:
    return _frame(
        template,
        kind,
        {
            "payload_id": cut.checkpoint_hash,
            "checkpoint_hash": cut.checkpoint_hash,
            "committed_sequence": len(cut.frames) - 1,
            "callback_sequence": cut.callback_sequence,
            "intent_sequence": cut.intent_sequence,
        },
    )


def _read_exact(stream: BinaryIO, count: int) -> bytes:
    parts = bytearray()
    while len(parts) < count:
        item = stream.read(count - len(parts))
        if not item:
            raise ValueError("delivery stream ended before its bounded batch EOF")
        parts.extend(item)
    return bytes(parts)


def write_batch(stream: BinaryIO, wires: Sequence[bytes]) -> None:
    """Length-prefixed finite batch; zero length is the explicit batch EOF."""
    if not wires or len(wires) > TRANSCRIPT_FRAMES or sum(map(len, wires)) > TRANSCRIPT_BYTES:
        raise ValueError("delivery batch exceeds its frame/byte budget")
    for wire in wires:
        decode_frame(wire)
    for wire in (*wires, b""):
        data = len(wire).to_bytes(4, "big") + wire
        while data:
            written = stream.write(data)
            if written is None or written <= 0:
                raise ValueError("delivery stream cannot complete its frame")
            data = data[written:]
    stream.flush()


def read_batch(stream: BinaryIO) -> tuple[bytes, ...]:
    wires: list[bytes] = []
    size = 0
    while True:
        count = int.from_bytes(_read_exact(stream, 4), "big")
        if count == 0:
            if not wires:
                raise ValueError("empty delivery batch")
            return tuple(wires)
        if (
            count > FRAME_BYTES
            or len(wires) >= TRANSCRIPT_FRAMES
            or size + count > TRANSCRIPT_BYTES
        ):
            raise ValueError("delivery stream exceeds its frame/byte budget")
        wire = _read_exact(stream, count)
        decode_frame(wire)
        wires.append(wire)
        size += count


class JobDeliverySender:
    """Parent adapter: only the fenced durable outbox can authorize cuts/ACKs."""

    def __init__(
        self,
        store: JobExecutionStore,
        binding: DeliveryBinding,
        context: Mapping[str, Any],
        *,
        requested_capabilities: Sequence[str],
        now_ms: int,
    ) -> None:
        require_delivery(requested_capabilities)
        self.store, self.binding, self.context = store, binding, context
        self.template = _template(binding, context)
        self.wire = store.delivery_checkpoint(binding, now_ms=now_ms)
        self.cut = cut_view(binding, self.wire)
        self.cuts = {self.cut.checkpoint_hash: self.cut}
        self.cut_wires = {self.cut.checkpoint_hash: self.wire}
        self.history: list[bytes] = []
        self.negotiated = False

    def negotiate(self, worker_hello: bytes, *, now_ms: int) -> None:
        self.negotiated = False
        self.store.delivery_checkpoint(self.binding, now_ms=now_ms)
        if worker_hello != _hello(self.template, self.cut):
            raise ValueError("worker delivery handshake/binding/schema/capabilities mismatch")
        self.negotiated = True

    def restore_batch(self, *, now_ms: int) -> tuple[bytes, ...]:
        if not self.negotiated:
            raise ValueError("recovery has not been negotiated by both sides")
        self.store.delivery_checkpoint(self.binding, now_ms=now_ms)
        wires = (
            _hello(self.template, self.cut),
            *payload_frames(self.wire, template=self.template, payload_id=self.cut.checkpoint_hash),
            _control(self.template, "RESTORE", self.cut),
        )
        validate_delivery_sequence(
            wires, self.binding, durable_cuts=(self.cut,), restored_cut=self.cut
        )
        if not self.history:
            self.history.extend(wires)
        return wires

    def committed_batch(self, *, now_ms: int) -> tuple[bytes, ...]:
        if not self.history:
            raise ValueError("restore must precede delivery progress")
        wire = self.store.delivery_checkpoint(self.binding, now_ms=now_ms)
        cut = cut_view(self.binding, wire)
        next_sequence = validate_delivery_sequence(
            self.history,
            self.binding,
            durable_cuts=tuple(self.cuts.values()),
            restored_cut=self.cut,
        ).logical_next_sequence
        logical = [json.loads(w) for w in cut.frames[next_sequence:]]
        wires = tuple(
            _frame(
                self.template,
                "MESSAGE",
                {"message": m},
                role="worker" if m["sender_role"] == "worker" else "parent",
            )
            for m in logical
        )
        candidate = [*self.history, *wires]
        cuts = {**self.cuts, cut.checkpoint_hash: cut}
        validate_delivery_sequence(
            candidate, self.binding, durable_cuts=tuple(cuts.values()), restored_cut=self.cut
        )
        self.history, self.cuts = candidate, cuts
        self.cut_wires[cut.checkpoint_hash] = wire
        return wires

    def packet(self, *, now_ms: int) -> tuple[bytes, ...]:
        """Send a complete bounded restore plus a durable committed continuation."""
        self.restore_batch(now_ms=now_ms)
        self.committed_batch(now_ms=now_ms)
        wire = self.store.delivery_checkpoint(self.binding, now_ms=now_ms)
        cut = cut_view(self.binding, wire)
        if cut.checkpoint_hash != self.cut.checkpoint_hash:
            extra = [
                *payload_frames(wire, template=self.template, payload_id=cut.checkpoint_hash),
                _control(self.template, "CHECKPOINT", cut),
            ]
            candidate = [*self.history, *extra]
            validate_delivery_sequence(
                candidate,
                self.binding,
                durable_cuts=tuple(self.cuts.values()),
                restored_cut=self.cut,
            )
            self.history = candidate
        return tuple(self.history)

    def descriptor(self) -> dict[str, Any]:
        """Trusted bootstrap pins; no wire assertion substitutes for fsync ownership."""
        return {
            "binding": asdict(self.binding),
            "capabilities": list(DELIVERY_CAPABILITIES),
            "initial_cut": self.cut.checkpoint_hash,
            "cuts": [
                {
                    "checkpoint_hash": c.checkpoint_hash,
                    "payload_sha256": c.payload_sha256,
                    "payload_size": c.payload_size,
                }
                for c in self.cuts.values()
            ],
        }

    def prepare_exchange(self, *, now_ms: int) -> tuple[bytes, ...]:
        """Detach/pin a finite packet before launch; this does not negotiate recovery."""
        if self.history:
            raise ValueError("a finite exchange needs a new sender; live transcript already exists")
        wire = self.store.delivery_checkpoint(self.binding, now_ms=now_ms)
        latest = cut_view(self.binding, wire)
        if latest.last_acknowledged_frame != self.cut.last_acknowledged_frame:
            raise ValueError("durable ACK cursor changed; construct a new finite sender")
        if latest.frames[: len(self.cut.frames)] != self.cut.frames:
            raise ValueError("durable cut no longer contains the admitted initial prefix")
        cuts = {self.cut.checkpoint_hash: self.cut, latest.checkpoint_hash: latest}
        wires = [
            _hello(self.template, self.cut),
            *payload_frames(self.wire, template=self.template, payload_id=self.cut.checkpoint_hash),
            _control(self.template, "RESTORE", self.cut),
        ]
        for raw in latest.frames[len(self.cut.frames) :]:
            message = json.loads(raw)
            wires.append(
                _frame(
                    self.template,
                    "MESSAGE",
                    {"message": message},
                    role="worker" if message["sender_role"] == "worker" else "parent",
                )
            )
        if latest.checkpoint_hash != self.cut.checkpoint_hash:
            wires.extend(
                payload_frames(wire, template=self.template, payload_id=latest.checkpoint_hash)
            )
            wires.append(_control(self.template, "CHECKPOINT", latest))
        validate_delivery_sequence(
            wires, self.binding, durable_cuts=tuple(cuts.values()), restored_cut=self.cut
        )
        self.cuts, self.cut_wires = (
            cuts,
            {self.cut.checkpoint_hash: self.wire, latest.checkpoint_hash: wire},
        )
        return tuple(wires)

    def accept_ack(self, wire: bytes, *, now_ms: int) -> bool:
        envelope = decode_frame(wire)
        if envelope["kind"] != "ACK":
            raise ValueError("parent expected a committed ACK")
        candidate = [*self.history, wire]
        validate_delivery_sequence(
            candidate, self.binding, durable_cuts=tuple(self.cuts.values()), restored_cut=self.cut
        )
        self.store.delivery_checkpoint(self.binding, now_ms=now_ms)
        body = envelope["body"]
        changed = self.store.acknowledge(
            self.binding.job_id,
            self.binding.run_id,
            self.binding.worker_generation,
            sequence=body["sequence"],
            message_id=body["message_id"],
            content_hash=body["message_hash"],
            now_ms=now_ms,
        )
        self.history = candidate
        return changed


class InteractiveDeliveryReceiver:
    """Worker adapter: full owner admission precedes activation and callbacks."""

    def __init__(
        self,
        driver: RC6InteractiveCallbacks,
        binding: DeliveryBinding,
        trusted_checkpoint: bytes,
        *,
        requested_capabilities: Sequence[str],
    ) -> None:
        require_delivery(requested_capabilities)
        self.driver, self.binding = driver, binding
        self.prepared = driver.prepare_job_restore(
            trusted_checkpoint, job_id=binding.job_id, worker_generation=binding.worker_generation
        )
        self.cut = cut_view(binding, trusted_checkpoint)
        self.cuts = {self.cut.checkpoint_hash: self.cut}
        self.template = _template(binding, driver.context)
        self.history: list[bytes] = []
        self.seen: set[str] = set()
        self.logical_messages = [json.loads(w) for w in self.cut.frames]
        self.failed = False

    def hello(self) -> bytes:
        return _hello(self.template, self.cut)

    def receive_batch(self, wires: Sequence[bytes]) -> int:
        if self.failed:
            raise ValueError("delivery receiver failed; replacement is required")
        candidate = [*self.history, *wires]
        projection = validate_delivery_sequence(
            candidate, self.binding, durable_cuts=tuple(self.cuts.values()), restored_cut=self.cut
        )
        pending: list[dict[str, Any]] = []
        applied = 0
        try:
            for wire in wires:
                e = decode_frame(wire)
                if e["delivery_id"] in self.seen:
                    continue
                if e["kind"] == "RESTORE":
                    self.driver.activate_job_restore(self.prepared)
                elif e["kind"] == "MESSAGE":
                    m = e["body"]["message"]
                    protocol = self.driver.restored_protocol
                    if protocol is None:
                        raise ValueError("worker owner has not been restored")
                    if m["sender_role"] == "worker":
                        if not pending or pending.pop(0) != m:
                            raise ValueError(
                                "delivery worker response differs from actual callback owner"
                            )
                    else:
                        protocol.accept(m)
                        pending.extend(self.driver.process(m, protocol))
                    self.logical_messages.append(m)
                    applied += 1
                self.seen.add(e["delivery_id"])
            if pending:
                raise ValueError("delivery callback responses remain undelivered")
        except Exception:
            self.failed = True
            raise
        self.history = candidate
        assert self.driver.restored_protocol is not None
        assert self.driver.restored_protocol._sequence == projection.logical_next_sequence
        return applied

    def committed_acks(self, trusted_checkpoint: bytes) -> tuple[bytes, ...]:
        if self.failed or not self.history:
            raise ValueError("ACK requires successful admitted recovery")
        admitted = prepare_job_checkpoint(
            trusted_checkpoint,
            job_id=self.binding.job_id,
            context=self.driver.context,
            session=self.driver.session,
            worker_generation=self.binding.worker_generation,
        )
        state = JsonResumeStateSerializer().loads(admitted.native_bytes)
        protocol = self.driver.restored_protocol
        if (
            protocol is None
            or self.logical_messages != admitted.payload["protocol_messages"]
            or state.strategy_state != self.driver.session.export_state()
        ):
            raise ValueError("ACK requires the exact applied committed owner cut")
        cut = cut_view(self.binding, trusted_checkpoint)
        self.cuts[cut.checkpoint_hash] = cut
        wires = []
        # A fully acknowledged cut returns its last exact ACK as completion proof.
        for sequence in range(
            min(cut.last_acknowledged_frame + 1, len(cut.frames) - 1), len(cut.frames)
        ):
            message = json.loads(cut.frames[sequence])
            wires.append(
                _frame(
                    self.template,
                    "ACK",
                    {
                        "message_id": message["message_id"],
                        "message_hash": message["content_hash"],
                        "sequence": sequence,
                        "checkpoint_hash": cut.checkpoint_hash,
                        "committed_sequence": len(cut.frames) - 1,
                        "callback_sequence": cut.callback_sequence,
                        "intent_sequence": cut.intent_sequence,
                    },
                    role="parent" if message["sender_role"] == "worker" else "worker",
                )
            )
        validate_delivery_sequence(
            [*self.history, *wires],
            self.binding,
            durable_cuts=tuple(self.cuts.values()),
            restored_cut=self.cut,
        )
        self.history.extend(wires)
        return tuple(wires)


def run_delivery_exchange(
    request: Mapping[str, Any], incoming: BinaryIO, outgoing: BinaryIO
) -> int:
    """One finite committed exchange; the bootstrap pins the durable parent cuts."""
    from openpine.runtime.rc6_worker_runtime import RC6InteractiveCallbacks, _session_from_request

    descriptor = request["worker_delivery"]
    require_delivery(descriptor["capabilities"])
    binding = DeliveryBinding(**descriptor["binding"])
    context = request["execution_context"]
    template = _template(binding, context)
    initial_hash = descriptor["initial_cut"]
    # HELLO needs only the independently pinned cut identity, before payload I/O.
    hello = _frame(
        template,
        "HELLO",
        {
            "capabilities": list(DELIVERY_CAPABILITIES),
            "inner_schema_hash": schema_hash("openpine.worker.protocol.v2"),
            "delivery_schema_hash": schema_hash(SCHEMA_ID),
            "checkpoint_hash": initial_hash,
            "limits": LIMITS,
        },
        role="worker",
    )
    write_batch(outgoing, (hello,))
    packet = read_batch(incoming)
    payloads: dict[str, bytearray] = {}
    seen: dict[str, str] = {}
    total = 0
    for wire in packet:
        frame = decode_frame(wire)
        if any(frame[k] != getattr(binding, k) for k in binding.__dataclass_fields__):
            raise ValueError("delivery bootstrap generation/identity mismatch")
        key = frame["delivery_id"]
        if key in seen:
            if seen[key] != frame["content_hash"]:
                raise ValueError("conflicting delivery packet")
            continue
        seen[key] = frame["content_hash"]
        body = frame["body"]
        if frame["kind"] == "PAYLOAD_BEGIN":
            payloads[body["payload_id"]] = bytearray()
        elif frame["kind"] == "PAYLOAD_CHUNK":
            chunk = base64.b64decode(body["data"], validate=True)
            total += len(chunk)
            if total > PAYLOAD_BYTES or body["payload_id"] not in payloads:
                raise ValueError("delivery restore payload exceeds its admitted budget/order")
            payloads[body["payload_id"]].extend(chunk)
    driver = RC6InteractiveCallbacks(_session_from_request(request), context)
    cuts: dict[str, tuple[bytes, CutReceipt]] = {}
    if not descriptor["cuts"] or len(descriptor["cuts"]) > TRANSCRIPT_FRAMES:
        raise ValueError("delivery bootstrap cut budget")
    for row in descriptor["cuts"]:
        wire = bytes(payloads.get(row["checkpoint_hash"], b""))
        if len(wire) != row["payload_size"] or raw_hash(wire) != row["payload_sha256"]:
            raise ValueError("delivery payload differs from its trusted durable bootstrap pin")
        prepare_job_checkpoint(
            wire,
            job_id=binding.job_id,
            context=context,
            session=driver.session,
            worker_generation=binding.worker_generation,
        )
        cut = cut_view(binding, wire)
        if cut.checkpoint_hash != row["checkpoint_hash"] or cut.checkpoint_hash in cuts:
            raise ValueError("delivery cut differs from its trusted bootstrap identity")
        cuts[cut.checkpoint_hash] = (wire, cut)
    initial_wire, _ = cuts[initial_hash]
    worker = InteractiveDeliveryReceiver(
        driver, binding, initial_wire, requested_capabilities=descriptor["capabilities"]
    )
    worker.cuts = {key: cut for key, (_, cut) in cuts.items()}
    worker.receive_batch(packet)
    latest = max(cuts.values(), key=lambda row: len(row[1].frames))
    write_batch(outgoing, worker.committed_acks(latest[0]))
    return 0
