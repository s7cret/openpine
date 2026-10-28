# History storage qualification · 2026-10-28

Completed the seven-part history storage stage on Python 3.14.8.
Source: `93a59597f7d779bceef815ac0517181536576b6f`; [CI 40681726120](https://github.com/s7cret/openpine/actions/runs/40681726120).

1. Separate legal length, proven retention and actually allocated cells.
2. Derive bounds from admitted integer IR and frozen inputs, with full-horizon fallback.
3. Allocate indexed trees geometrically; keep samples for later expansion.
4. Share charged history memory across primary/private owners, preview, resize and
   restore. Historical and replay workers apply their saved memory policy,
   including a reused replay batch session.
5. Recompute and verify the plan on restore; validate retention/counters before
   publishing state. Preview changes individual cells; grown private trees keep
   their allocation charged until disposal.
6. Expose primary/private estimates through requirements CLI/API and launch UI.
   Changing-window allocation errors identify call/context and retain the source diagnostic span.
7. Qualify the composition and retain every existing observation.

**1230 tests / 140 modules**; all **1207 previous test IDs** retained, 23 added.
**2419 previous conformance observations unchanged**: 1601 executions and 818
expected rejections. All 79 immutable bundle files and previous spec bytes match
the baseline. No new language cases or claims of TradingView equivalence.
CI: **53 successful jobs**, 1230 tests on each OS, one wheel, 18 installed suites
per OS; every previous helper remains in its existing suite. Local installed
acceptance covers signals, windows, targets and contexts outside checkout.
Local native qualification explicitly aggregates 1220 successful cases from the
unchanged production build with 10 freshly executed cases: the corrected
five-case technical lifecycle module and the five cases skipped after its old
predicate failed. Only `tests/test_technical_state.py` differs from that run;
Git file hashes and every wheel member prove production equivalence. The old
failed gate remains recorded. Full CI acceptance reruns all 1230 cases on each
OS against the final source and its wheel.

The two old first-allocation test expectations now use a one-cell budget, because
indexed storage starts with two cells. Their IDs remain; new tests also check
growth failure before mutation. The legacy three-cell factory forwards the
shared account keyword. The one-million-cell lifecycle test receives sufficient
byte allowance only for that assertion, retaining strict `HISTORY_STORAGE`;
the new independent byte-budget tests keep their small rejection limits.

The same 10002 scheduled events, three markets, 70014 checked output points and
runtime restart were measured in sequential isolated processes:

| Metric | Baseline | Current |
|---|---:|---:|
| Primary allocated cells at end | 820006 | 60 |
| Final checkpoint bytes, all contexts | 469486 | 13372 |
| Peak RSS bytes | 114319360 | 99565568 |
| Elapsed seconds | 7.649 | 5.101 |

This is one indicative comparison on one machine, not a statistical benchmark.

The archived baseline was also asked to run the million-event composition with
only short changing lengths. It stopped after 33333 primary bars and 699993
verified output points with `HISTORY_STORAGE`. The current implementation completes
the full long scenario and additionally grows the length to 4096. Thus the
before/after timing table uses an identical shorter scenario; the old build cannot
provide a completed million-event result. This expected legacy rejection is
recorded separately, not counted as a current failure.

The long composition verifies **1000002 events and
7000014 output points**, all seven signals in three markets,
restart at bar 166666 and abrupt growth to length 4096. Peak RSS
100630528 bytes; final checkpoint 578464
bytes. Every point is compared to independent cyclic integer/rational formulas.
The long run restores fresh runtime owners within one process; actual isolated
worker pause/resume is verified separately by native and installed pipelines.

Browser acceptance runs one saved IR on markets B and A, checks independent
VWMA/MOM values, the resource estimate before market selection and after reload,
and no JavaScript errors. The screenshot and raw receipts are in the local proof
directory; their hashes are published in the evidence.

Four preliminary source attempts were superseded during audit: replay policy
propagation, permanent preview-growth charging, the legacy budget test factory
and the independent cell/byte test allowance were tightened before final
qualification. Their CI runs were explicitly cancelled, not counted as accepted
passes. Full CI binds the final frozen source. Reused local checks retain their
original provenance and the verified production equivalence described above.
Superseded logs remain in separate local proof directories.

[Storage contract](history-storage.md) ·
[machine-readable evidence](evidence/history-storage-20261028.json).
