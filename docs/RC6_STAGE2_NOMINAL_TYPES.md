# Stage 2: nominal language and locked type imports

Status: published implementation candidate, pending coordinated Linux CI.
**Stage 2 remains `in_progress`; full_stage2_accepted=false.**

## Source preservation

The host baseline is `668d009a5754fac138431a83dfc7eb83f73f408e`, freshly fetched
from `origin/release/5.0.0rc6`. It already includes the intrabar candidate
`648083aed1b11cba805e0c49095b797bac016327` and the newer publication documentation.
Its permanent CI run [34147625999](https://github.com/s7cret/openpine/actions/runs/34147625999)
completed successfully. That result applies to the baseline, not this candidate.

| Component | Nominal candidate |
|---|---|
| Pine2AST | `7107e797e32c360d2fce90a6416297ef2076351e` |
| PineLib | `ad235a8d7befc091808b20927139436eb4bdbf40` |
| Ast2Python | `30512014a45db1be29def8fa8c83eb35b0d4b4c2` |

Other component pins remain those of the fetched host baseline. Each changed
library retains functional, test and documentation commits. Pine2AST also has an
explicit integration commit retaining both intrabar histories: the pinned
`5adfc88` and remote `ac3b1a0` diverged. Useful overload validation and all 47
remote regression cases were preserved alongside the pinned lexical fixes and
tests; the remote tree was not copied over the local candidate.

## Implemented behavior

The normal frontend binds nominal UDT/enum identities, exact method receivers,
UDT constructor/copy/default facts, field-level `varip`, enum titles and qualified
type spans. Methods on different UDTs or different generic collection types have
independent return types and declaration/callsite state identities.

Ast2Python lowers those contracts to PineLib's existing transactional state and
heap. UDT fields, references, shallow copies, reference history, enum history,
loop values, arrays/maps containing UDTs and JSON continuation use one semantic
owner. PineLib validates typed heap schemas and restores ordinary fields while
retaining admitted `varip` fields. An ordinary field is never promoted merely
because another field is `varip`. Retained intrabar allocations no longer collide
with retried constructor allocation IDs after an abort.

The existing library linker now projects UDT and enum declarations, field and
parameter types, constructors, returns and generic type arguments using exact
locked publication identities. Different publishers/revisions with the same
library and type names remain distinct. The new profile is
`same_version_reference_types_v4`; old scalar/array/collection profiles keep their
names. Public parameter, field and return types may not expose private nominal
types, including unused public functions. Transitive source changes invalidate
the linked source and generated artifact. Private helper implementations and
example code are not treated as public library interfaces.

These are engineering expectations derived from the
[Pine library rules](https://www.tradingview.com/pine-script-docs/concepts/libraries/)
and [v5 library rules](https://www.tradingview.com/pine-script-docs/v5/concepts/libraries/),
not external TradingView execution exports. `tradingview_verified=false`.

## Once and independently corrected assumptions

`once` retains its ordinary persistent completion flag. Pine documents rollback
on historical order-fill recalculation as well as realtime updates. Making the
completion flag intrabar-persistent would change that behavior. The new runtime
and compiler matrices cover actual deferred `ORDER_FILL_RECALC`, final commit,
abort, UDF/loop contexts and checkpoint continuation.

Two old frontend tests assumed omitted UDT fields were required arguments. Pine
specifies implicit field defaults. Their assumptions were corrected in a
separate test commit `17c358ed649f4e047d3b72fe155e73dd4dc6d33e`, retaining the old
diagnostic coverage with a real extra-argument case. The independent sources and
original failed assertions are in Pine2AST's `docs/STAGE2_NOMINAL_LANGUAGE_REVIEW.md`.
No expected trading result was copied from the runtime.

## Local verification and actual failures

| Check | Python 3.11 | Python 3.13 |
|---|---|---|
| Contracts full suite | 557 passed | 557 passed |
| Pine2AST focused producer matrix | 493 passed | 181 passed, plus 20 catalog/release tests |
| Reviewed typed linker and existing collection profile tests | 31 passed | 31 passed |
| PineLib full suite | 480 passed, 3 failed | 480 passed, 3 failed |
| Ast2Python new nominal matrix | 48 passed | 48 passed |
| Ast2Python full suite | earlier 669 passed, 3 failed before final additions | 681 passed, 3 failed |

The focused rows overlap; they must not be summed into a unique test count.
The candidate adds at least 107 parser/linker, 78 runtime, 48 compiler and 36 host
cases. Linux collection must verify the exact baseline node-ID superset before
the permanent inventory is explicitly updated.

Frontend: 152 Vitest cases passed and production build passed. Static architecture
check passed for all eight local components with no ownership violations.

Actual wheel and sdist builds completed for all eight components on Python 3.11
and 3.13. Archive inspection found six missing Pine2AST reference JSON resources;
package-data and sdist declarations were fixed separately. Three real artifact
tests now pass on both interpreters, including default catalog/matrix validation
from an extracted wheel under `python -I -S` and a missing-resource negative case.
This does not claim the final wheel-only installation gate or source-identical
production artifacts; the coordinated CI must still verify the exact candidate.

Observed local failures remain visible:

- Windows has no `fcntl`/`resource`; mandatory provider, host and some release
  tests fail collection. Engine collection also imports the provider boundary.
- Both optimizer full runs had 54 failures and 227 passes on Windows; these are
  recorded as failures, not accepted results.
- PineLib's three distribution checks fail on Windows sorting/symlink/executable
  mode behavior. Compiler's three infrastructure checks fail on Git subprocess
  access/symlink privileges. Those tests remain mandatory in Linux CI.
- Initial contracts collection found an editable engine checkout's `tests`
  package. Installing that engine normally fixed the test environment; both full
  contracts reruns passed without changing tests.
- Initial typed-linker tests exposed incomplete qualified spans and enum title
  syntax. Parser fixes resolved all seven failures. Independent review then
  found an unused-export private-return leak; two negative regressions now pass.

## Remaining acceptance

This candidate does not accept any full Stage 2 criterion. Complete overload and
version matrices, full builtin oracle coverage, library method exports and mixed
language-version execution, recursive reference-valued `varip` fields, broader
NA/bool semantics and complete nominal checkpoint member registries remain open.
The new host tests in `rc6_tests/test_rc6_nominal_types.py` must run through the
permanent Linux environment, including real protected workers and both broker
transports. Coordinated package builds and the frozen Stage 1 corpus are also
mandatory. No Stage 3 work or final RC6 acceptance is claimed.

Performance was not measured in this stage.
