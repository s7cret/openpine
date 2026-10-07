# Committed worker delivery v1

The user agreed the additive `openpine.worker.delivery.v1/1.0.0` form before
consumer implementation, per specification section 11.1. Logical
`worker.protocol.v2/2.3.0` schemas and sealed messages remain unchanged.

`GeneratedJobExecution.delivery_sender` opts into `delivery_v1` and
`committed_job_v1` after the existing SQLite WAL/FULL job owner publishes a
complete cut. The sender pins job/run/session, physical worker/generation,
stack/producer identity, and independently admitted checkpoint/payload hashes.
It rejects an expired lease, stale generation or foreign worker before launch
or publication. `deliver_committed` connects this sender to the existing
systemd/bubblewrap launcher and unit cleanup. There is no in-process fallback.

`InteractiveDeliveryReceiver` admits the complete native/Pine/job graph before
activation. It restores protocol and ACK cursors without replaying callbacks,
checks the actual callback responses against a committed suffix, and emits ACKs
only for its exact applied committed owner cut. Parent ACK admission validates
message/hash/role/cut/cursors and persists contiguous advancement in the existing
job transaction. Exact delivery repeats do not reset owners; conflicting repeats
refuse. A changed durable ACK cursor requires a newly pinned finite sender.

The binary stream has bounded length-prefixed frames and explicit batch EOF.
Raw chunks are 128 KiB, encoded envelopes 256 KiB, aggregate payload 16 MiB,
transcript 32 MiB, frames/cut views 4096 and JSON nesting 64. The existing canonical
sealer and registered delivery validator own the schema/framing semantics.
Bootstrap cut hashes come from the parent durable owner; untrusted wire assertions
alone do not prove fsync. The worker verifies those independent pins before owner
activation. ACKs bind the correct receiving role of each logical frame.

This implementation exchanges finite historical committed cuts. The native
producer commits its authoritative broker/Pine cut before a replacement worker
verifies the restored callback suffix. It does not enable provisional/live resume
or change legacy `closed_bar` transport and capabilities. Both peers explicitly
negotiate the recovery capabilities; an old worker fails before RESTORE delivery.

The trusted helper closure adds the delivery, job checkpoint, protocol, generated
backend and packaged worker runtime modules. Protected qualification requires
new exact source/helper/candidate bindings and actual unit/cgroup/family checks.
Source-unit and ordinary separate-process results do not establish installed,
normal/sdist, protected INT04/INT05 or full product acceptance.
