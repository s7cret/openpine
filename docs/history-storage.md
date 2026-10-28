# History storage

`openpine.history-storage.1` separates the legal length (1..100000), proven
retention and allocated cells. The legal language range is unchanged for v1–v6.
Only the changing `window.*` and `swindow.*` kernels use reduced retention.
Public fixed-length contracts and immutable Wiki bundles retain their identities.

The runtime derives an integer interval from admitted IR and frozen inputs.
Positive modulo, checked integer arithmetic, min/max, immutable declarations and
specialized function parameters can prove a smaller upper bound. Both branches
contribute; reassigned variables, unsupported expressions, overflow and analysis
exhaustion keep the full horizon. The proof does not depend on observed lengths.
For example `% 4 + 1` retains four samples; an unknown length retains up to
100000 even after a long sequence of length-one queries.

Prefix rings grow by append up to retention plus their anchor. Indexed extrema,
trends and offsets start with one leaf and grow geometrically, capped by proven
retention. Resizing remaps retained samples using their global position, retaining
NA positions and latest ties. The allocation is based on reached samples, never
on the largest length seen so far. Short windows can expand later without losing
data. The previous one-million-cell cap per evaluation still applies.

One runtime owns a shared charged-history account for the primary evaluation and
each written request, including separate requests to the same market. In workers
its limit is half the saved `RunPlan.memory_mb`; direct sessions default to a
512 MiB process policy. Each reserved cell is conservatively charged 640 bytes,
covering wide exact prefix integers as well as smaller index slots. This is a
history allocation policy, not a measurement or guarantee of total RSS. Arrays,
outputs, decoded checkpoint envelopes and reader caches remain subject to their
existing limits and the supervisor's unchanged process/aggregate RSS enforcement.

The shared account includes resize overlap, temporary restore buffers and changed
cells in preview overlays. Preview borrows confirmed buffers without copying
them. Disposable allocations release their charges on disposal; failed restore
explicitly releases its temporary owner. Resource errors identify the written
call and context; runtime diagnostics retain the Pine source span. A worst-case
estimate exceeding a limit does not reject a short run that never allocates it.

Checkpoints bind the derived plan digest to their program and frozen settings.
Every dynamic state records retention, validated against the recomputed bound.
Actual geometric capacities are reconstructed from retained counts; JSON cannot
select a smaller capacity or invent allocation counters. Validation completes
before a fresh runtime publishes restored state. Execution identity changes make
old implementation checkpoints incompatible, following the existing run policy.

CLI `requirements`, the preparation API and the launch UI show the sum of primary
and private changing-window estimates and the conservative restore peak. These
estimates cover changing windows; they explicitly do not claim to estimate all
process memory. Input changes recompute the plan and stale responses are ignored.

`python -m tools.history_storage_soak --directory <new-dir>` checks all seven
signals in three scheduled markets, every output against independent cyclic
integer/rational formulas, durable serialization and fresh runtime restoration,
and an abrupt length increase to 4096. The default is 1,000,002 candle events.
It uses streaming generated sources and a checking sink, without keeping output
rows in memory. It reports elapsed time, peak RSS, actual charged cells and
checkpoint sizes. Installed acceptance also runs a shorter composition on both
supported operating systems. Exact TradingView comparison remains a separate
stage; previous version-evidence limitations still apply.
