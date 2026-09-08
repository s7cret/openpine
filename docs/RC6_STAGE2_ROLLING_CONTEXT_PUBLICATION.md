# Stage 2 rolling statistics and library context publication

The verified block adds callable-result inference through nested control flow,
varip arrays of admitted UDTs, rolling-window statistics, finite numeric array
search boundaries, and producer-reconstructed library result provenance. The
compiler consumes that provenance; the runtime retains ownership of reference
state, rollback and statistical kernels. These changes preserve the eight
component boundaries.

Exact host `d8f165088c27d29d2d7d6b467c31c52f648b17eb`, tree
`de8ba1e027f6a3bd11077b83467b4bb5179cc1f4`, passed coordinated Linux run
[34176337191](https://github.com/s7cret/OpenPine/actions/runs/34176337191).
Python 3.11 and 3.13 each passed 9685 tests with zero failures, errors or skips.
All 8470 prior tests remain; 1215 tests were added. The five existing provider
network deselections remain unchanged. Sandbox, protected workers, architecture,
package builds and frontend checks passed, including 152 Vitest and 22 Node tests.

Both interpreters produced 1160 observations against independent manual tables:
47 original builtin, 75 scalar, 44 transcendental and 66 rolling cases, each on
five paths. These engineering fixtures are not TradingView execution exports.
The twelve frozen Stage 1 observations and original corpus remain unchanged.
The published independent review records exact artifact and source identities.

## Exact tree integrations

| Repository | Release integration | Tested source |
| --- | --- | --- |
| pine2ast | `7ff9fd31580b16145d7e07a00b580e425df98c9c` | `5b90a733e548a13bd5dc88c922e7ca397059c456` |
| ast2python | `89e644128640e552b5c22ceb21fc37b1d653a9ab` | `7be8a705a1285d73401677a3589e96c6ecfcbd38` |
| pinelib | `ef95595ede8688f3f3cb40bc87739e5eb21aab7a` | `ff3fda01541795c793068bced47a0d195bf27e37` |
| OpenPine | `ac118eecb4a69f45eb6935de7ea91dc46facfcbd` | `d8f165088c27d29d2d7d6b467c31c52f648b17eb` |

Each integration has the previous release and tested source as ordered parents.
The complete merged tree equals the verified tree. References advanced without
force; functional changes, new tests, reviewed fixture corrections, integrations
and this publication remain separate commits.

Preserved failures include nested-control qualifier counterexamples, forged
rolling checkpoint owner metadata, initial rolling-window mismatches, and native
Windows resource/fcntl limitations. Two old setup fixtures were corrected in
separate reviewed commits without changing their assertions. The 22 normative
cases changed only 66 target-identity fields and the outer hash; their sources,
expected semantics, source maps and historical producer fixtures were retained.
No expected values were regenerated from observed runtime output.

Language-state branch cleanup is already independently recorded in
`verification/language-state-cleanup-final-execution-review.json`. Cleanup of
the branches integrated here is pending guarded archive and deletion.

Stage 2 remains in progress and all four final criteria remain unaccepted.
Array concat, revised constant rounding/variadic binding, UDF constant values
and exported library methods are outside this tested source. Existing UDT and
enum import support is distinguished from still-missing exported methods.
Performance was not measured by this publication.
