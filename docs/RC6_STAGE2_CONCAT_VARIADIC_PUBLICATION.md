# Stage 2 concat and constant variadic publication

Array concat returns the first array handle and preserves aliases through named
namespace and method receivers. Modern finite constant rounding and canonical
repeated argument facts now reach the normal variadic ABI with eager source-order
evaluation. The producer owns admitted types and argument facts, the compiler
owns lowering, and the runtime owns array state and numeric execution.

Exact host `4d952ccac1f051bd75e7a4ef2dd7644cdf379b83`, tree
`7bd4473f07803ed445dc51371dd2fe38f5a85370`, passed coordinated Linux run
[34181992287](https://github.com/s7cret/OpenPine/actions/runs/34181992287).
Python 3.11 and 3.13 each passed 10683 mandatory tests with zero failures, errors
or skips: all 9685 previous tests remain and 998 were added. The five existing
provider network deselections are unchanged. Sandbox, protected workers,
architecture, real package builds, 152 Vitest and 22 Node tests passed.

Each Python produced 1660 actual observations against independent manual tables,
covering 1410 unique case/path pairs. Concat contributes 50 assigned cases from
30 manual rows on five paths in both full and compact modes: 500 observations,
2000 final snapshots, 600 trial executions and 400 checkpoint restores. The
previous 1160 observations and twelve frozen Stage 1 cases remain unchanged.
Manual engineering fixtures are distinct from TradingView execution exports.

## Exact tree integrations

| Repository | Release integration | Tested source |
| --- | --- | --- |
| pine2ast | `0b67e9ed80faa762100c6a1f282c1a11608f023a` | `7170736f2730316407f0d896447b08e76f5c9c08` |
| ast2python | `5c199789a0cdc640de7be1b13e5ad170dfd96c93` | `95a14be4be8987faafb3e7629c744e698fd9f134` |
| pinelib | `4360ae790324458f14c57317adfae0f4b028ca4e` | `d33fdea2ff3d2fa3c567c2c83da08664c8937fc1` |
| OpenPine | `ba4e65789be0f612c6eb28a39cb1eb4b53e3bac2` | `4d952ccac1f051bd75e7a4ef2dd7644cdf379b83` |

Each integration retains the previous release and tested source as ordered
parents, with the complete tested tree preserved. Source changes, new tests,
reviewed fixture identity corrections, inventory review and publication stay in
separate commits. The single existing concat fixture identity changed only after
independent derivation from two literal min/max contract metadata changes; its
assertions and expected array semantics were preserved. Initial malformed
variadic flag failures and Windows-only control failures remain in evidence.

The preceding rolling/library-context branch cleanup is independently complete.
Branches integrated here await guarded archival and deletion. Persistent CI
operations history remains available.

Stage 2 remains in progress. Constant UDF/context evaluation, float comparison
and string version policies, additional array observations, exported library
methods and numeric array aggregates are outside this tested source. None of the
four final Stage 2 criteria is declared accepted by this bounded publication.
