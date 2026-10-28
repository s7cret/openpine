# Host memory and checkpoint recovery

This stage adds host RAM admission to the existing R6 coordinator. It preserves
the one-repository layout, saved RunPlan resource limits, existing runtime
semantics for Pine v1–v6, and separate immutable specification bundles.

## Admission and resource ownership

`HostAdmission` is shared by coordinator preparation/read slots, historical
workers and finite replay workers. Windows uses `GlobalMemoryStatusEx` physical
availability; Linux uses `MemAvailable` and a visible cgroup-v2 memory cap. A
sample is cached for at most 250 ms. Unavailable metrics are explicit: configured
slot/memory limits and process RSS supervision remain active.

The system reserve is 1/16 of effective physical RAM, clamped to 128–1024 MiB.
Admission includes reservations for work that has not materialized in RSS yet.
Historical worker reservations shrink as its RSS materializes, avoiding counting
those bytes again against OS availability. Coordinator/replay reservations remain
conservative until their operation exits. After pressure, admission requires an
additional 64 MiB margin to avoid rapid stop/start around the boundary.

A historical job denied RAM remains `queued`; the API decorates its phase as
`waiting_resources`, with available/required bytes and a retryable reason. No
attempt is claimed and no retry is consumed. The scheduler retries automatically;
pause/cancel remain usable. Preparation/read admission returns the existing
`COORDINATOR_BUSY` diagnostic with host details. Metadata-only slots remain
available. Existing processes keep their saved RSS budgets. An OS sample is not
a guarantee against another application allocating memory immediately afterward.

## Compact exact SMA state

`openpine.sma-prefix.1` stores one exact integer prefix anchor and chronological
retained source doubles. Restore reconstructs exact integer prefixes at their
physical wrapped positions and verifies their sum against the saved total.
Defined-sample count, capacity, scalar types and the prefix anchor range are
checked against admitted state. NA, skipped calls and numerical recurrence are
unchanged. The previous closed `items/count/capacity` representation is also
readable when program, implementation, settings and derived history-plan
identities match. Saved plans from another implementation are still rejected.

## Block checkpoint codec

`openpine.checkpoint-tree.1` is a storage envelope over closed JSON tokens in
immutable blocks of at most 64 KiB. Strings are split into bounded pieces;
references, lengths, token counts, nesting and scalar kinds are validated. No
pickle, dynamic import or JSON-selected constructor is involved. Runtime and
output checkpoint roots use the same ObjectStore as other artifacts.

Each block's hash and encoded size are verified before parsing. Admission checks
decoded ownership plus a conservative temporary parsing allowance before each
block; constructed containers and strings are metered throughout reconstruction.
Historical loading splits the saved job's restore decode allowance 3:1 between
runtime and output envelopes, which coexist until their atomic consumers run.
Replay loading derives its allowance from the saved replay memory policy.

This bounds parsing and aggregate decoded-tree footprint. The primitive tree is
still materialized before runtime restore; runtime buffers then undergo their
own quota checks and atomic publication. Export traverses an existing primitive
checkpoint tree; it does not claim a lazy runtime serializer. Small records retain
the legacy JSON representation. Legacy JSON is accepted only when its encoded
size fits the conservative pre-parse allowance; oversized legacy records fail
explicitly before parsing, rather than silently exceeding the memory budget.

GC and backup follow only the codec's explicit block references. They also retain
the output prefix's immutable Parquet/CSV/trade objects. Corrupt/missing references
fail closed, and publication fencing continues to select one durable cursor.

## Retained-byte lifetimes and UI

Derived history policy `openpine.history-storage.3` keeps the logical cell quota
separate from byte ownership. Legacy EMA and MACD release seed sample bytes when
the seed is computed; MACD's historical logical cell count remains unchanged.
Selected previews reconcile borrowed seed ownership only after transfer;
discarded previews cannot release confirmed samples.
Restore reserves the historical MACD logical count separately and charges only
pending samples, so completed seeds cannot cause a false byte-quota failure.

String histories charge a 256-byte slot plus four bytes per retained character,
including replacement/preview/restore lifetimes. The requirements estimate keeps
the conservative maximum string size. Arrays retain their separate payload and
copy-on-write accounting within the same job ledger.

The web panel shows host availability/system reserve, worker RSS and sampled
peak, charged state/limit/peak, checkpoint encoded size, and the RAM waiting
reason. Small values use KiB. Estimates shown before launch remain separate from
measurements. Checkpoint size covers unique state/output envelope blocks and
their root; referenced candle/output datasets are separate immutable data.

## Reproducible checks

- `python -X utf8 -m tools.managed_recovery_soak --directory <new-dir>` runs
  100,000 primary bars, two private markets and 30 outputs through actual
  JobManager/apps.worker processes. It kills a worker after disk publication,
  kills its coordinator before catalog acknowledgement, resumes a third attempt,
  and compares every persisted Parquet value with the cyclic rational oracle.
- `python -X utf8 -m tools.realtime_memory_soak --directory <new-dir>` checks
  v1–v6, 256 bars, 64 unconfirmed updates per bar, two requested markets, skipped
  EMA calls, rollback and restore inside an open bar. v4–v6 include bounded varip
  arrays/counters. Every committed output is checked; retained bytes and the last
  quarter's RSS must plateau within explicit allowances.
- `python -X utf8 -m tools.unified_storage_soak --directory <new-dir>` retains
  the previous 1,000,002-event composition and now checks every value read back
  from Parquet, in addition to the online oracle and complete unique ranges.

Run with Python 3.14.8. Local heavy checks are sequential. TradingView equivalence
remains a separate validation stage.
