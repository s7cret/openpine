# OP-26 / OP-27 / OP-12 — independent and verifiable optimization

## Implemented, not whole-task acceptance

Optimizer source `95570459e50492dea8872b0a25f094e29d3e821f` contains three task commits:
`d2816781a7a020a7d32d9d61253ab27dca8a62b5` (result/ranking integrity),
`facb6a05687a75eca2df7483558457f10033f840` (warmup, input isolation, explicit identities),
and `95570459e50492dea8872b0a25f094e29d3e821f` (mapping fallback hash and fixture alignment).
Full optimizer CI `34003198123` verified 281 cases on each of Python 3.11 and 3.13,
including original process-containment tests, before creating its canonical RC6.
No process-group safety test or implementation was disabled to obtain this result.

Failed, cancelled, incomplete or error-bearing runner results are not ranked as
profitable trials, even when they contain finite profit metrics. Both accepted
response contract names obey strict availability/hash metadata validation. Nonfinite
primary objectives fail; undefined optional ratios are omitted rather than poisoning
unrelated finite metrics. Actual zero metrics do not trigger fallback defaults.
Old ranks are cleared before recomputing the leaderboard.

The engine runner applies per-trial effective_pre_bars including explicit zero and
rejects unsupported controls. Nested parameters, input bars and supplied strategy
instances are copied per execution. A factory still owns the responsibility to return
a fresh engine; arbitrary external globals cannot be proven deterministic by copying.
`strict_identity=True` requires explicit nonzero SHA256 source/data/config identities.
These caller attestations do not by themselves verify wheel contents. Development
fingerprints include plain captured state and structural bytecode rather than type-only
or address-bearing repr; opaque/cyclic state requires explicit fingerprints.

## Requests in optimizer trials

A trial creates a new run/session execution identity. Its request manifest must be
bound to that identity without changing its input data. The host now verifies the
original manifest and execution context first, permits only run/session ID changes,
then reseals and validates a detached manifest. Corruption or a foreign manifest
cannot be laundered by hashing it again. Canonical source envelopes remain unchanged.

The applied configuration hash is calculated after this binding and recorded in the
persisted run identity and response. Responses retain both source and trial manifest
hashes. The public BacktestRunConfig/converter now forwards request_manifest rather
than silently dropping it, while preserving private mutable inputs. This remains an
explicit-preload API; automatic data discovery is separate OP-08 work.

Tests compare repeated and reordered sensitive-input trials with ordinary backtests,
including complete intent tapes, closed trades, final equity and score-ledger hashes
under the same admitted trial context. Two mandatory protected-process cases also
compare serial and concurrently launched trials in interactive and bulk modes. Their
actual joint CI results are recorded in the publication receipt, not assumed here.
Run-specific identities are intentionally distinct; semantic outcomes, not differently
named run envelopes, are compared across different trial IDs. Seeds/ranges not supported
by this host are rejected explicitly, not claimed applied.

## Retained verification and limits

The permanent read-only RC6 workflow runs all seven library suites: provider external
network cases remain explicitly excluded; OpenPine is still native plus selected
regressions, not its complete inventory. It also retains protected worker, frontend
build and backend API checks. Source pins are exact; wheels with identical version
names can still differ. Full release coverage/immutable installation is not complete.

`RC6_REVIEW_36.json` and its Markdown companion preserve all original task IDs and
record implemented scope, remaining acceptance and evidence paths. Existence of a
test path is not a claim that an unobserved test passed; receipt-based verification
is separate. No unsupported feature is marked complete merely because it now fails
early. The ledger is not a replacement for the original specification.

Winning-trial replay from the user-facing report, locked train/validation/warmup,
full seeded external-runner determinism and holdout acceptance remain open. There was
no speed or memory benchmark. Private copies increase isolation, not guaranteed speed.
The former local containment failure reflects this container's missing procfs support;
the unmodified full containment suite passed in supported GitHub-hosted Linux.
