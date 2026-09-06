# OP-07 / OP-20: causal pending price-entry brackets

Date: 2026-09-06. Implementation notes; actual joint CI results are recorded separately.

## Implemented

The broker now captures explicit exit instructions for existing pending limit,
stop and stop-limit entry instances, as it already did for market entries. An exit
activates at the real parent fill, with relative levels based on that fill price.
Cancelling or replacing an entry/exit cannot attach its discarded instruction to
a later unrelated entry with the same public ID. Partial exits retain the existing
quantity and reservation rules.

The historical fill scanner traverses a forward-only path. On each monotone OHLC
segment, it selects the nearest eligible stop/limit crossing, processes fills or
stop-limit activation, and recomputes the next event. New protection begins at the
current price; an earlier high/low is never revisited. Stop-limit limit touches
before activation cannot become retroactive fills. A limit already marketable at
stop activation uses that activation point, not an unnecessarily worse limit.
Newly attached marketable absolute exits likewise start at the current price.

OCA cancellation follows price-event order, not which distant order happened to
be inserted first. Limit-verification penetration changes the trigger threshold,
not the limit execution price. The normal chart-close callback no longer replays
the previously consumed path when fill recalculation or on-close processing is on.

Bar Magnifier processes only the first chart opening in the initial open pass;
subsequent subbar opens stay in their chronological positions. Gaps between bars,
including magnifier bars, remain discrete and do not invent intermediate prices.
Closing-tick-only execution uses the final chart close, not an earlier subbar close.
Close-only and observed realtime-tick execution do not acquire synthetic crossings.
Trailing evolution remains handled by the existing scanner and is not newly claimed.

## Deterministic tests

The full local broker suite passes 680 cases on Python 3.13.5: 58 added scenarios
plus the existing suite. Three old negative price-entry assertions were upgraded
to positive or no-backward-fill checks, not removed or skipped.

OpenPine adds 37 compiled Pine cases: 24 long/short, entry-type and calculation-mode
combinations; two no-retroactive-stop checks; five historical named-when versions;
and six real protected worker cases (three price-entry types in both IPC modes).
The 31 nonprocess cases pass locally. The six process cases are mandatory in joint
CI and are not claimed run locally without Bubblewrap/AppArmor.

Checks compare complete intent tapes and resulting broker trades/equity between
bulk and interactive, exact entry/exit prices and bars, quantities and open positions.
These are synthetic specification examples, not exported TradingView oracle data.
No full-project coverage or browser visual acceptance is implied.

## Compatibility and remaining work

The pinned engine is 891141faf482c76ed1f85a7f4b0076f26ed63336. Host and worker must
be updated with the source pins and modules recompiled when the capability surface
changes. The previous market-only/pending-price rejection is superseded for this
explicit from_entry subset. Existing request/checkpoint/optimizer/runtime protection
code is not replaced by this block.

All-entry/unqualified persistence, repeated-entry relative-level allocation,
trailing, leg-specific metadata, v6 mixed absolute/relative levels, risk controls
and indexed trade methods remain open. Full fill-phase/commission/margin parity,
external event-oracle comparison, full isolated-job recovery, automatic request
loading and immutable production installation remain separate review tasks.

This change can increase work when a bar crosses many active orders, since it
recomputes eligible boundaries after events. No throughput, memory or whole-backtest
speedup has been measured. It prioritizes causal correctness over unmeasured speed.
