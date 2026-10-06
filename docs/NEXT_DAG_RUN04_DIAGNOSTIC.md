# Next functional DAG owner: RUN-04 protected-worker resume

This is a bounded read-only diagnosis against the host source based on 7320127. No worker, service, build, native campaign or new acceptance framework was started. INT05 owns its current integration freeze and heavy slot. This document selects a concrete functional gap after the current integration/qualification gates; it does not claim every prerequisite is missing or accepted.

## Concrete gap and reproduction

The [remaining specification](OPENPINE_5_0_REMAINING_SPEC_2026-10-06.md#lifecycle), RUN-04, requires a new protected worker to restore a full job checkpoint and immutable inputs and continue from an acknowledged cursor. Today `openpine.runtime.isolated_run.run_isolated_artifact` raises `IsolatedRunError("RESUME_UNSUPPORTED_FOR_WORKER_PROTOCOL")` whenever `resume_state` is non-None, before input admission or worker launch. `openpine.runtime.worker_capabilities.WORKER_CAPABILITIES` is exactly `("closed_bar",)`; requesting `checkpoint_v1` is rejected. This is an explicit implementation boundary, not an inference from a red publication JSON.

The actual source API probes were:

```python
from openpine.runtime.isolated_run import run_isolated_artifact
from openpine.runtime.worker_capabilities import validate_requested_capabilities
run_isolated_artifact(b"not even compiled", bars=[], config=object(), resume_state={})
# IsolatedRunError: RESUME_UNSUPPORTED_FOR_WORKER_PROTOCOL
validate_requested_capabilities(["closed_bar", "checkpoint_v1"])
# ValueError: unsupported worker protocol capabilities: ['checkpoint_v1']
```

The empty state/invalid artifact intentionally proves the unconditional early refusal; it is not a valid checkpoint round-trip or a sandbox qualification. Existing `rc6_tests/test_rc6_worker_capabilities.py::test_outer_resume_remains_an_early_explicit_error` preserves this current behavior. The real probe, timestamp, source hashes are retained outside sources in `next-dag-run04-read-only-probe.json`. Generated-session checkpoint export and grammar-level CHECKPOINT/RESTORE transitions do not establish full broker/transport/job resume.

## Dependencies and owner sequence

| Dependency | Existing owner and required functional output | Why RUN-04 needs it |
|---|---|---|
| Current INT04 fixes and INT05 integration/qualification gates | Existing verification/campaign and protected-worker owners; exact reviewed code and cleanup evidence | A retry or killed controller must not leave its old worker/family active. This remains separate from adding resume semantics. |
| RUN-02 typed external restore | `backtest_engine` production decoder with runtime state owners; public export → real JSON bytes → new engine → public restore → continuation | A worker cannot resume opaque dictionaries or old-process objects. Validate the complete input before replacing state. First confirm the existing positive path and add only actual decoder gaps. |
| RUN-03 atomic full-job cut | PineLib, broker, Provider snapshots, OpenPine jobs/runtime and shared contracts | The checkpoint must bind runtime/history/references, broker reservations/intents, immutable data/request identities, output cursors and job generation consistently. |
| RUN-04 protocol/admission | Existing contracts and `worker_protocol`, `worker_capabilities`, `isolated_run`, `isolated_worker`, `rc6_worker_runtime` | Negotiate an implemented versioned operation, validate exact identities, restore a new real protected worker and fence old generations. |
| RUN-05 effect application | Existing broker/job/output owners | Distinguish transport redelivery, job retry and a new run; deduplicate stable effect identities without deleting legitimate distinct events. |

After gates, the first useful functional slice is a supported committed boundary: export a complete typed job checkpoint, terminate the old process, start a new protected worker, restore and continue a fixed immutable event stream. Extend the existing public API/protocol and registered types; do not add a parallel runner, decoder in fixtures, or a verdict-only engine. Choose the schema and producer/consumer responsibility before changing multiple owners. Advertise the resume capability only once its real operation is implemented.

## Concrete validation for that future change

Compare uninterrupted and interrupted/restored runs using independently fixed expected orders, fills, quantities, cash/equity, event ordering, output identities and digests. Inject kills before/after checkpoint publication, between intent and acknowledgment, after a fill, on a callback and during output upload. Reject corrupt state, foreign ABI/library/data revision and stale worker generation; preserve the last valid checkpoint and diagnostics on refusal. Check the exact affected worker unit/cgroup and a live neighbour, following the existing INT04 recipe. Both interactive and bulk paths must use real protected workers.

This is an open implementation task with explicit prerequisite owners. No resume acceptance, new capability publication, service/security change, PR publication or heavy execution is represented by this diagnosis.
