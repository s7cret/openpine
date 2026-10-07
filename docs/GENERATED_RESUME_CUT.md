# Generated native committed cuts

`BacktestEngine.run(..., execution_backend=RC6GeneratedExecutionBackend,
resume_state=bytes)` admits one local committed parent-bar cut. The native JSON
owner validates the complete broker/accounting/statistics graph, config identity,
bar prefix and explicit tick schedule. The existing generated/PineLib owner
validates artifact/runtime/input/config/run identity, series/history, slots,
var/varip, references, request child graphs, visual/alert state, transcript and
callback/intent sequences before any live reset or output callback.

The shared cut then binds committed primary histories and callback coordinates
to the native input. Each recorded `ORDER_FILL` cause must match an ordered actual
broker fill by parent-bar index, order identity, exact causal price and execution
time. Realtime fills use the admitted event's tick timestamp; historical fills
use the parent timestamp. Repeated legitimate fills with equal IDs/prices consume
distinct ordered fill indices. Native close-activated scans can coalesce several
fills into one recalculation; the binding admits this ordered subsequence and
requires every filled parent to have a recalculation when configured. It rejects
fill callbacks when `calc_on_order_fills` is disabled. Checksums establish internal
consistency, not authenticity against replacement of every graph and checksum.

Export refuses provisional generated bars and pending aborts. Required
`finalize_bar` precedes protocol/bar-end publication even if optional callbacks
are disabled. Fresh unused owners and receivers with previous committed state
use the same detached admission path. Late graph corruption or a mismatch
between individually valid Pine/broker graphs rejects before broker reset,
strategy evaluation, provider reads or public callbacks.

The caller supplies the immutable generated artifact/source bundle and admitted
data/request dependencies whose identities the existing owners bind. The public
checkpoint contains JSON values and registered native types, with no links to
objects in the producer process. Source-overlay process proofs are local evidence;
they do not qualify rebuilt installed packages or protected workers.

`GeneratedJobExecution` adds a bounded historical job envelope over these same
native/Pine owners. `JobExecutionStore` uses the existing job SQLite WAL/FULL
transaction to publish a complete protocol prefix, native checkpoint, embedded
immutable protocol artifact bytes and callback/intent/output/ack cursors together.
Provisional frames stay private. Acknowledgments are contiguous and bind exact
message IDs/hashes; exact transport repeats are idempotent, while conflicting
repeats and gaps reject. An explicitly new run starts separate sequences. Job
retry can resume the same run, and worker replacement advances a monotonic
generation. Every outbox read, frame write, publication and ack checks the active
run, live lease and generation. Recovery truncates only the unpublished suffix.

Portable job bytes can restore a new ledger/engine/session without reading the
producer's database or artifact paths. Complete owner/protocol/artifact admission
precedes the import transaction; import refuses existing ledgers so it cannot
roll back acknowledged effects. Protocol restoration validates the full prefix
and constructs the next cursor without re-emitting historical messages. The
generated graph uses its existing JSON bytes inside the protocol artifact, which
preserves native floats and Pine values despite the outer protocol's scalar domain.

The supported job boundary is a historical committed parent bar. The existing
local native tick bridge remains separate; this durable job path refuses explicit
ticks and export-disabled runs. The protocol outbox provides durable ordered
delivery with exact IDs. An external consumer must acknowledge only after its
own durable/idempotent application; external exactly-once delivery is not promised.

RUN04's protected worker path remains open:
`worker_capabilities.py` still advertises only `closed_bar`, and the interactive
driver does not implement `CHECKPOINT`/`RESTORE` recovery. RUN04 capability and
supervisor changes require that complete job restore path first. Protected fault
qualification also requires the existing storage/qualification authorization.
Compiled immutable request snapshots retain their historical-only guard.
