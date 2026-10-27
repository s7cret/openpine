# Historical lower-candle execution

`openpine.bar-magnifier.1` is an opt-in historical reconstruction for Pine v1–v6.
The immutable Wiki references attest `strategy(use_bar_magnifier=...)` only in
v5/v6. Earlier versions use explicit OpenPine settings. The default is off;
an explicit false source flag conflicts with an enabled configuration. A true
flag requires a pinned lower snapshot. Received/live replay rejects that flag.
`request.security_lower_tf` remains a separate, unimplemented stage.

## Data and coverage

The primary and lower snapshots are immutable Parquet inputs referenced by
`openpine.run-plan.7` / `openpine.job-request.8`. No candle arrays are embedded in
jobs. Both inputs must have the same symbol, provider, instrument environment
and trading rules. The first profile accepts divisible UTC minute intervals
from 1 through 1440, with a strictly smaller lower interval. Existing acquisition,
coverage catalog and provider tasks produce the lower snapshot too.

Preparation streams both inputs and validates every primary interval before
creating a job: unique ordered lower slots, complete coverage, matching OHLCV
(volume tolerates relative 1e-12 / absolute 1e-9 rounding). Malformed candles,
duplicate timestamps, inconsistent price envelopes and complete OHLCV conflicts
are errors. Rows outside a selected primary window are ignored. Research windows
reuse the immutable lower snapshot and consume only their own intervals.

`missing=reject` rejects gaps. Explicit `missing=parent_ohlc` models the **whole
affected primary candle**, without replaying the available partial lower rows.
The result reports covered/fallback parent counts, effective mode, lower counts,
both input identities and last execution provenance. A fallback never masks a
price conflict. This option is independent of the provider's gap policy.

## Execution model

Each complete lower candle supplies four **model samples**: open, nearer extreme,
other extreme, close; the high is first on an equal-distance tie. There is no
interpolation between samples. A crossing order executes at a sampled price
according to the shared sampled-price lot broker. For example, a sell limit of
110 can fill at the next sampled price of 120. These samples and their assigned
times reconstruct a historical path; they are not received market ticks.

The shared broker retains lot reservations, partial exits, OCA, stop-limit,
trailing, fees, risk and trade observations. Every execution event carries parent
time, child time/timeframe, point ordinal, sampled price, modeled availability,
sequence, fallback and simulation qualification.

Pine executes once at primary close and, when enabled, after an atomic fill
batch. `calc_on_every_tick` does not create ordinary Pine passes at model samples.
The closing-price processing phase remains one bounded batch. Commands produced
by a fill recalculation wait for the next sample. Ordinary state rolls back to
the last primary commit; `varip` survives intermediate passes; history and plots
commit once per primary candle.

Intermediate frames contain only cumulative **visited** model samples and
volume from closed lower candles. Requested contexts advance only as their own
closing times become available; `lookahead_on` is rejected in this profile.
Historical bar-state flags retain the existing historical phase policy.

## Recovery and interfaces

The checkpoint records the immutable input hash, completed primary cursor,
lower snapshot cursor, inside-parent point ordinal, active cumulative frame,
requested-context cursors/anchors, runtime, `varip`, broker, phase counters and
output prefix. It can be published inside the first primary candle, when zero
primary bars are committed. SQLite migration 15 preserves existing checkpoint
rows and admits this cursor. Every restore validates the deterministic path.
Checkpoint publication seals immutable output/journal blocks to avoid repeatedly
copying a growing CSV tail. Existing publication fences/retry rules apply.

```powershell
python -m apps.openpine job --source examples/bar-magnifier/v6.pine `
  --bars examples/bar-magnifier/parent.json `
  --lower-bars examples/bar-magnifier/lower-high-first.json `
  --magnifier examples/bar-magnifier/configuration.json --state-dir .runtime/magnifier
```

`--lower-bars` saves the child snapshot and replaces the example's placeholder
ID. With an existing snapshot, supply its actual ID in the configuration and
omit `--lower-bars`. Resume uses the saved inputs/settings. HTTP jobs, prepared
launches, experiments, searches and walk-forward use the same `magnifier`
configuration. The web form supports upload/existing snapshot/provider loading,
strict/fallback policy, phase overrides, checkpoint frequency and saved copies.

The examples provide two independently specified paths with identical primary
OHLCV: high-first yields fills 100/120 and equity 1020; low-first yields 100/80
and equity 980. This qualifies the local model, not TradingView equivalence.
