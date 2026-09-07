# Stage 2 retained abort and host checkpoint candidate

The native runtime can retain valid varip changes after abort without consuming
a successful callback sequence. Its checkpoint now carries a bounded, replayable
ordered abort witness when state or control differs from the last successful
transcript. A restore replays the runtime owner's actual begin/abort operations
and strictly validates the resulting state before replacing any live owners.

Full transcript version1.1 and compact version2.1 bind callback/deferred mode and
the actual publication boundary. Existing native checkpoint1.0 data and legacy
entry prefixes remain readable. New checkpoint1.1 data supports the explicit
pending record. Admission uses the same registry and aggregate resource limits
for nested request contexts. Invalid proofs or resource overflows preserve the
previous live state, control, sequence and checkpoint; no user callback runs
during restore.

The proof decoder permits one narrow transient invariant: a working array slice
may temporarily outlive the shortened working backing array before abort repairs
it. Schema, element type, nominal declarations, parent ownership and acyclic
reference structure remain checked. Normal checkpoint and final graph decoding
remain strict. This is not a permissive public heap loader or an authenticity
claim for attacker-replaced checkpoints.

## OpenPine boundary

OpenPine delegates publication classification to
`RuntimeTranscript.is_publication`; a modern callback whose phase happens to be
`BAR_COMMIT` is still a callback. Bound runtime mode must agree with the host's
direct/event receipt. Host export and import explicitly reject a native pending
abort, including when the host cursor is closed: outer generated-session
checkpoints still describe completed host publication boundaries.

The 28 new host cases cover both Pine v5/v6, full/compact transcript modes,
direct/deferred callbacks, continuation, rehashed mode conflicts, callback phase
classification, native pending-abort rejection and atomic owner preservation.
Their native Windows import stops at unavailable `fcntl`; Linux execution is
mandatory and has not yet accepted this candidate.

## Source and evidence

PineLib's frozen block contains six production files, four new test files with
317 cases and one document. Independent focused tests pass on Python3.11/3.13.
Each complete owner run has1280 passes and three known native Windows packaging
failures, with no errors or skips; the prior966-case inventory is retained.
The immutable source bundle, implementation receipt and independent review are
separate from ongoing varip nominal-array work.

The previous successful registry run and the pending scalar run do not accept
this newer source. It requires a fresh retained inventory and complete joint
Linux execution with the unchanged Stage1, sandbox, architecture and frontend
gates. Stage2 remains in progress.
