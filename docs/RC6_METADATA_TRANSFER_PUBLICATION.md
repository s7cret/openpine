# Metadata transfer and per-fill absolute exits — publication receipt

Date: 2026-09-06. Status: published and jointly verified; original local-only status superseded.

## Source selection, not a blind overwrite

The supplied `OpenPine_RC6_Order_Metadata_Local_Candidate_2026-09-06.zip` was verified against its checksum inventory. Archive SHA256: `3aad2918f718a83ff352709466d15346e0925eb9fa6c646324fb9880d157e869`. Its host was based on runtime `eb56964d75ff371a0755c34a3a92e146c1c78070`; the current remote added documentation at `bbf338bd1ea3635e4efb3fa6529962cd11fb2bcb`. The integrated host is based on that newer commit and retains its documentation.

Meanwhile Contracts and Engine had already published a different, flat ExitIntent 2.6.0 representation and per-opening-fill absolute exit quantities. Importing the archived nested `exit_metadata`/`exit_semantics_version` wrapper as a second 2.6 dialect would break interoperability. The published representation and models are retained, with compatible archived changes ported explicitly. No new claim is made that every archived commit is a release ancestor.

| Archived change | Selection in the integrated source |
| --- | --- |
| Contracts `6cf3180768b1` | Superseded by published `d30f5986d2fc` / `5950e0f99214`; flat metadata fields and strict old-version validation retained |
| Engine `bce477b20aed` | Use published `289579edb4ab` / `3d8c84c13fc5`; supplement ORDER_FILLED context rather than reverting updated Fill/Trade models |
| Engine `1825efe35142` | Port positional and NA-aware binding to `d5d9c8e6cd1c` |
| Engine `374a72ea4db4` | Preserve its required-order-events test file byte for byte in `d5d9c8e6cd1c` |
| Pine2AST `a0e548365eba` | Publish the original commit with its unchanged SHA |
| OpenPine `4e7a225fd993` | Port admission and all 29 metadata scenarios to `ba6cfe9f5116`, then correct the real bulk test's representation handling without removing assertions |
| OpenPine `026bf5a9ba0e` | Preserve as the historical local-only report in the original delivery, not as a false current publication status |

`port-map.json` in the delivery records the full original and selected identities. The old incompatible wrapper is not installed or advertised as an additional production format.

## Exact coordinated revisions

Tested host: `0509399dfa4f4478ae1815ed0a862e40dca9867c`; tree `d0abfe681f514e056c86c32cebf6f8ca7b0ab318`.
Required Contracts: `5950e0f99214e60b64162d074ed47f1e4bbc7141`.
Required Engine: `d5d9c8e6cd1cad40dd5402bcf4521519a1de522e`.
Required Pine2AST: `a0e548365eba137c247758a3da53c5514398043d`.
The complete sibling set is `RC6_LIFECYCLE_SOURCES.json`.

Four host source commits remain separate: `ba6cfe9f5116` (compatible transfer), `bd47e20522be` (absolute-lot integration matrix), `88b8a07fbbdd` (actual bulk output availability) and `0509399dfa4f` (real-process fixture/representation correction). Six source commits are newly published in this transfer, including the original parser commit and new engine integration. Five preexisting Contracts/Engine commits were reused, not reauthored. The complete coordinated delta since the prior trailing source has eleven source commits; a subsequent receipt commit changes documentation only.

## Implemented metadata behavior

Generic comments, alert_message and disable_alert pass through the existing delegation/replay/buffer/order/fill/result chain. Per-leg comment_profit/comment_loss/comment_trailing and alert_profit/alert_loss/alert_trailing select the executed leg's metadata. Empty strings are explicit overrides; NA is absence/fallback, not the literal string "na" or a fabricated empty message. Disable-alert preserves the trade and its numeric result.

New trade records capture entry and exit metadata at execution. Reusing an ID or changing a later order does not reconstruct and overwrite those historical values. Existing native broker resume and copied state preserve the captured data. Published metadata tests also cover pending entries, amendments and reversal behavior. This is not full process/IPC restart recovery.

The newly integrated ORDER_FILLED event snapshots public order ID, fill index, executed leg, comment, message and suppression/eligibility flags before callbacks. The broker remains the owner of execution semantics. `alert_eligible` describes captured eligibility; it is not proof that a Telegram/webhook alert was sent.

## Next block: absolute exits use individual opening fills

The already published broker change `c34c7ef456bb` is integrated rather than lost during the archive transfer. Named absolute limit/stop/bracket exits now use the same per-opening-fill quantity/reservation mechanism as relative and all-entry exits. The new host matrix covers long/short, TP/SL/bracket, named/all-entry scope, qty_percent/qty/both and reservation amendments. It compares full closed trades and execution events between real compiler/runtime/broker paths with in-memory transports; eight separate process cases cover the actual isolated boundary.

Synthetic long example: opening fills of 2 at 100 and 6 at 110, `limit=120` and `qty_percent=50`, produce separate closes of 1 and 3 at 120, leaving 1 and 3. The short case is mirrored. When qty and qty_percent are both supplied, the qty argument wins. Reducing the first exit's reserve to 50% allows the second exit to reserve the remainder separately for both opening fills. Closing comments and alert flags stay associated with each execution.

The full broker suite includes native resume for this state. FIFO/ANY attribution across different entries is not closed by these tests. Expectations are explicit synthetic fixtures, not exported TradingView execution traces.

## CI-discovered bulk omission and fixture corrections

The first host run `34049758857` correctly blocked publication. Downloaded XML on both Python versions shows six failed new process cases, not silently removed old tests. Two revealed a real product omission: the bulk serializer discarded the broker's `available_outputs`. The added field now carries the actual broker set through the sealed result and parent hydration; it is not inferred from requested flags. Four new regressions distinguish uncollected data from a collected but valid empty output.

Two process assertions expected mapping-shaped metadata where the public bulk API hydrates nested values as SimpleNamespace. Tests now read either documented representation while keeping exact message/flag assertions. Two more expected the second market entry to fill at the next bar, although `calc_on_order_fills` legitimately issued it during the first fill callback. The fixture now explicitly requests the second fill's trigger price; four additional long/short recalculation checks preserve the same quantity/price/event expectations. The runtime is not changed to impose the erroneous test timing.

No sandbox restriction, lifecycle assertion, existing test selection or worker case was disabled. The failed reports remain separate evidence; they are not counted as accepted passes.

## Observed verification

Joint verification and publication: https://github.com/s7cret/openpine/actions/runs/34050729011
Engine integration: https://github.com/s7cret/backtest_engine/actions/runs/34049392236
Original parser transfer: https://github.com/s7cret/pine2ast/actions/runs/34049014669
Previously published Contracts/Engine: runs 34048390738 and 34048605934 respectively.

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 557 | 557 |
| PineLib | 229 | 229 |
| Pine2AST | 371 | 371 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 1005 | 1005 |
| Optimizer | 281 | 281 |
| MarketData Provider, non-network selection | 601 | 601 |
| OpenPine functional native / selected cases | 1090 | 1090 |
| Functional total | **4506** | **4506** |
| Review accounting, not semantic conformance | 37 | 37 |
| Total | **4543** | **4543** |

Both downloaded JUnit sets contain zero failures, errors or skips. The eight new protected-process variants are present and passing on both interpreters. Frontend XML contains 152 passing Vitest cases, and the Node log records 22 passing cases. Actual type/build/API, lint and Python distribution gates passed.

The net increase over the coordinated trailing snapshot is 391 functional cases: 120 Contracts, 157 Engine, 33 parser and 81 host. This includes reused fresh library changes, not 391 newly authored tests in this transfer. Host additions are 29 transferred metadata cases, 48 absolute-lot cases and four bulk-availability cases. The initial failed reports, standalone component runs and overlapping local selections are not added to this passing total.

The permanent native workflow's exact installation, library, provider, sandbox, native/selected and lint/build/OpenAPI commands are executed by the temporary verification workflow. This avoids a weaker copied acceptance path. Six full library functional suites run, the provider explicitly excludes five external-network cases, and OpenPine runs native plus its existing affected-path inventory. The 37 review-accounting checks are not Pine semantic tests. Repeated interpreter runs and standalone component runs are not additional unique cases.

The eight new real-worker variants cover metadata and per-fill absolute exits in interactive/bulk, alert suppression and long/short. Full original parser release/performance cases and optimizer process-containment cases run in CI; their former local environment failures are not an acceptance substitute. Frontend uses the exact backend source bundle and its exported OpenAPI, with actual TypeScript/Vite build before Node package checks. Browser visual/canvas testing, coverage thresholds and external TradingView oracle acceptance remain open.

Downloaded archive SHA256 digests, JUnit counts, exact source plans, raw commit objects and Git trees were independently checked. The final publication bundle has the same tested source as both interpreter artifacts.

| Artifact | SHA256 |
| --- | --- |
| Final Python 3.11, 9994568541 | `d278e281aa2a21980648521e8960cc83e91670f5ea27c7d95798d58a7b9d4a96` |
| Final Python 3.13, 9994586180 | `ee33186730ef5a19ef575d3462bcff14093b7991470f261bc5dc8f83154c1564` |
| Frontend, 9994594030 | `116d1830ddbebf8a8c9862648216c6a90456db7f7bb4b62388e834d712c8a4d5` |
| Successful publication retry, 9994670155 | `a8e7d4877c2323be3a8b2a75b1de10b67109c4b76a180981629a189076e0c758` |
| Engine integration, 9994082664 | `9edf4c7b07531a33f759433d9c40a62a07d358411c5cc5ae013e6277f559accd` |
| Parser transfer, 9993986691 | `58a3a8eb34fb66269894439a7e3c9dfc8c4ce34572830db0944eed8908f7d242` |
| Retained Contracts, 9993800520 | `5919cac5d3b35a3f93cefafa114741904b522eccfa4b668b652039eb3522130a` |
| Retained Engine source, 9993861592 | `571d7a83eb10677438bd960c4bb7c977ceb82147dd0f67e07ab956086615114a` |

## Preservation and deployment

Eight important host files remain byte-identical to `bbf338bd1ea3`: request_data, request_transport, generated_checkpoint, worker_capabilities, rc6_marketdata, rc6_config, isolated_worker and optimizer/isolated_runner. The UI tree is also unchanged. rc6_worker_runtime is intentionally different only by the added actual-output-availability serialization. Blob identities and the exact two-line runtime change are recorded in delivery evidence.

All verification jobs passed before the release update. The first Actions publication push was rejected only because its token could not update rc6-native.yml. After fresh expected-base and exact-tree reads, the authorized connected API advanced RC6 with force=false. Retrying the publication job verified that expected head, then archived and deleted only its maintenance branch. The retry completed successfully; no permissions, tests or sandbox protections were changed to make it pass.

The downloaded final receipt and fresh remote reads confirm exactly main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. Historical heads are unchanged. The same-name tag ops/rc6-metadata-transfer-20260906 preserves host maintenance tip `4d21fa443b5a65cb1384a558a56afd720ab0b520`. Corresponding parser and Engine tags preserve `882fb70e02c62940f86030a7e51bf17a7f5d493c` and `199fbdb31328c5f2c5372ff1721511c90aeb5a4a`. One-shot transfer helpers are not part of the RC6 runtime tree.

This receipt and progress update are documentation-only additions after the tested source. A later documentation SHA is not claimed to have a new full CI pass unless separately observed. The original 36-task files retain their named historical snapshot; this receipt supersedes the implemented OP-06/07/14/20/30 portions, and the delivery contains a complete updated 36-task snapshot without claiming full task acceptance.

Update the coordinated sources and recompile artifacts against the admitted host surface. The tested flat 2.6 intent is incompatible with the unpublished archive's nested wrapper; do not install that old candidate over current RC6. Identical package version strings do not identify the same code. Source snapshots in the delivery reproduce the verified trees; they are not an autonomous immutable production installer.

## Remaining scope

Strings are captured from command execution; arbitrary deferred evaluation of an alert expression at a later order fill is not implemented. TradingView documents that timing separately. External delivery, placeholders and exactly-once notification behavior after full job recovery remain open. Captured metadata is not full alert parity.

Fixed-stop/trailing competition, FIFO/ANY, risk/indexed methods, complete historical overloads and runtime matrices, automatic request discovery/UDF/live contexts, full broker/IPC/worker restart, immutable wheel delivery, winner replay/holdout and browser UX are separate work. No whole-backtest performance improvement or completion of all 36 tasks is claimed. The all-36 snapshot retains 29 partial, six unverified and one accepted task (OpenPine branch preservation only).

Primary semantic references, not execution-oracle evidence:
- https://www.tradingview.com/pine-script-docs/concepts/strategies/
- https://www.tradingview.com/pine-script-docs/concepts/alerts/
