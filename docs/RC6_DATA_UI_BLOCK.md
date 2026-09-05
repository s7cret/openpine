# OP-21 / OP-09 / OP-28 data, chart context and parity UI

## Implemented scope

Offline imports now use the provider's strict shared row decoder, explicit UTC timestamp units and explicit missing-volume policy. Parquet input iterates selected columns in bounded batches, without a whole-file intermediate list. Invalid rows are rejected even outside the chosen range or after max_bars. Data is not rewritten. The selected return list still materializes and the full file is scanned; concurrent file-write or immutable snapshot proof is not implemented. See marketdata-provider docs/OFFLINE_INPUT.md for migration notes.

Provider hour intervals translate to Pine minute notation (1h -> 60, 4h -> 240); uppercase M remains calendar months rather than being misread as minutes. Unsupported multiple-month intervals are explicitly rejected by the provider, not claimed implemented. The worker passes the admitted pointvalue to PineLib. A compiled hourly Pine strategy reads timeframe.period and syminfo.pointvalue and produces a verified order in both transports. Full instrument metadata, including hardcoded crypto type and derived base currency, remains a separate gap.

TV parity requests and polling callbacks are guarded by selection/lifecycle generations. Older detail, queued-run, list and preview responses cannot replace the newer selection. Stopped/restarted poll loops cannot apply stale results/errors or terminate a new loop. Deletion/unmount invalidates relevant results; a late preview cannot overwrite a report period. Canceled is terminal. Duplicate run submission is prevented while a POST is pending. Summary failures are retryable. Existing AbortSignal-based latest-request consumers remain compatible. The new epoch tokens suppress UI writes but do not abort HTTP or cancel a server job.

The UI tests use actual Vue setup/watch/unmount with an in-memory renderer plus deferred network promises. They are not browser/visual/E2E acceptance. The existing native CI remains read-only and now includes the deterministic provider suite. The same read-only workflow runs UI unit/component tests, type checks and a production build, then checks packaging and browser API paths against OpenAPI exported by its verified backend job. There is no unrelated or fabricated contract fixture. Existing release/coverage gates are not replaced.

## Request integration remains open

Actual compilation probes for request.security and request.security_lower_tf still fail at A2P_PINELIB_INJECTION (unhandled source-parameter injection). The compiler/PineLib lowering and child-expression context must be implemented before claiming an integrated request provider. This block does not simulate requests by replacing Pine source strings. The separate na(strategy.position_avg_price) type-evidence probe also remains open.

## Verification and source set

The exact compatible sibling set is RC6_LIFECYCLE_SOURCES.json. This includes provider 4e269bb7e3d389cae6214da9e17898c32c7ead15; update dependencies together, not by matching version strings alone. Local Python 3.13.5 evidence: 601 deterministic provider cases and 8 chart/session/in-memory cases passed. Five existing live-network provider cases and two new real worker cases require their respective external environments. CI publication receipts, when available, supersede this pre-publication local status; no new CI result is fabricated here.

The global 36-task acceptance is still open. This block does not complete OP-08 requests, full OP-09 metadata, immutable installation, checkpoints, all historical Pine signatures, whole-run cancellation, browser UX or TradingView oracle comparison. No whole-backtest speed or memory improvement was measured.
