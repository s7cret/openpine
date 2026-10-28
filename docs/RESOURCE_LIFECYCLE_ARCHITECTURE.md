# Run resource lifecycle

This stage changes admission, process ownership and persistence. It adds no Pine
syntax or TradingView equivalence claim. Python 3.14.8 and the pinned specification,
SQLite, DuckDB and Parquet profiles remain the qualification environment.

## Ownership and supervision

`JobManager` owns every finite historical, replay and replay-batch process.
`Supervisor` implements launch environment, sampled RSS, finite deadline,
heartbeat freshness and attempt fencing, cancellation, kill and reap. The app
retains publication validation and batch-member deadlines. Coordinator shutdown
kills and reaps its registered processes. Resource reservations outlive a worker
until it has been reaped; an unsuccessful launch releases its reservations too.

The existing worker parent watchdog remains active. RSS is sampled, rather than
a native OS hard cap. This stage does not introduce subprocess trees inside a
worker, nor promise kernel-enforced memory isolation.

## Disk admission

Short write preflights sample under the registry lock without publishing and
fsyncing a zero-lifetime reservation. Long operations retain their leased records.

Free-space admission complements the immutable ObjectStore quota. Each owning
store has `.disk-reservations/`: a serialized registry and a kernel-locked lease
for each active reservation. A crashed process loses its kernel lock; a later
admission reclaims the stale record. Reservations from independent processes in
the same store are counted together.

The default floor is 64 MiB. Worker admission preflights 896 MiB, retaining
128 MiB while its actual bounded columnar operations reserve 640 MiB for their
512 MiB spill allowance and output. Candle CSV blocks and output/journal writes
check headroom before growth. Checkpoint objects and prefix copies check their
write size. Backup and restore reserve the copy size on the destination volume.
Read-only DuckDB connections without an explicit scratch directory disable
spilling instead of writing into an unaccounted working directory.

Queued historical work stays queued without consuming an attempt when the
preflight refuses admission. A worker's disk-wait envelope preserves the latest
committed checkpoint. Coordinator preparation and background acquisition propagate disk pressure as
retryable waiting; replay/batch disk waiting does not increment live execution
failures. The configured immutable-object quota remains a distinct nonretryable
cap. Private CSV, Parquet and DuckDB temporary files are removed
after a stopped process is reaped. Historical attempts hold a lease throughout
execution; recovery waits for an orphaned attempt to release it before starting
a replacement. Disposable closed-trade SQLite indexes also check write headroom
and are reaped under this lease, with retries for temporarily retained handles. Replay publication remains transactional and
keeps its previous immutable prefix on failure.

This is a cooperative budget. Unrelated programs, distinct stores on the same
volume and new input sizes can change free space after admission. ENOSPC remains
a real failure to handle, not a condition that admission can make impossible.

## Checkpoint records and memory

The legacy tree codec remains readable. The new
`openpine.checkpoint-records.1` envelope contains a structural header and explicit
immutable references for independently exported history rings, kernels and array
heap objects. The normal inspection/export API can still produce a primitive
tree; persisted historical and replay workers request deferred records.

Save exports and validates one record at a time. Restore retains the small
header and decodes one record when its consumer requests it. It does not retain
all primitive rings and kernels alongside the reconstructed native runtime.
Each runtime continues to build its replacement privately and commits only
after its complete closed schema, identities, cursor and ownership charges pass
validation. A failed composite restore closes the affected driver/contexts.

Save preflights exactly the same bounded decoder used by load. The pinned
`job_memory` restore allowance is divided between runtime and output; a record
and its structural header each receive half of the corresponding allowance.
An un-restorable record is rejected before the checkpoint root is accepted.
Unreferenced immutable objects from an interrupted save are GC candidates.
The lifetime logical-cell cap and charged native-state budget still apply.

This is streaming between bounded records. An individual ring/kernel/heap object
is still materialized and validated as a unit. It does not admit an arbitrarily
large individual state under a small memory policy.

Backup, audit and GC follow only the closed record/tree schemas. They never infer
references from arbitrary strings in a program or a user object.

## Scheduling and waiting

Demand is grouped into live/replay, ordinary history and research lanes, with
FIFO within each lane, round robin between lanes and aging of waiting work.
Research uses the same persisted historical job queue. Live retries carry a
stable request identity; abandoned retry tickets expire after two seconds. The
queue is bounded. With multiple workers, a pending live demand has priority for
the last available slot; other slots continue to serve history/research. With
one worker, a running job must finish or be stopped before another can start.
No running calculation is preempted to satisfy a new demand.

Historical status and live/replay status expose RAM, disk and worker waiting
reasons. Busy API replies remain retryable. UI labels distinguish those reasons.
The scheduler does not silently lower a saved run's memory or thread policy.

## Qualification flow

CI builds one shared wheel. Native tests retain their twelve OS/shard jobs;
the 18 installed suites per OS have at most twelve concurrent runners. Four long
proof jobs start after the build, with at most two runners, alongside remaining
qualification: managed crash recovery, realtime rollback/plateau, million-event
persisted oracle and mixed full-store recovery.

The final `openpine.ci-acceptance.2` receipt requires all native/specification/
installed receipts and all four recovery reports. It checks source and wheel
identities, report hashes, original counts and required recovery transitions.
A missing proof, a substituted wheel or a report from another source fails the
final gate. Runner availability still controls actual latency; the change bounds
competition rather than promising a fixed build duration.
