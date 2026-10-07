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

RUN03's full job cut remains open: durable job/output acknowledgments, worker
generation and transport recovery are not carried by this local contract.
`worker_capabilities.py` still advertises only `closed_bar`, and the interactive
driver does not implement `CHECKPOINT`/`RESTORE` recovery. RUN04 capability and
supervisor changes require that complete job restore path first. Protected fault
qualification also requires the existing storage/qualification authorization.
Compiled immutable request snapshots retain their historical-only guard.
