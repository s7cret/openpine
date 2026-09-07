# Stage 2 scalar joint publication

The 75 independently authored scalar cases now pass all five execution paths on
Python 3.11 and 3.13. Version availability for float conversion is enforced by
the catalog owner while the float type remains available in earlier versions.
The previous 47 manual builtin cases and frozen Stage 1 corpus remain unchanged.

Exact candidate `20e2959e0090446177f5a7d52b27e65234fb7b5c`, tree
`afbd13813cd3f7f637f6d1236e9e5800cb770ef4`, passed full Linux run
[34168428781](https://github.com/s7cret/OpenPine/actions/runs/34168428781).
Each Python executed 7754 tests: all 7243 previous node IDs and 511 additions.
There were zero failures, errors or skips; the five existing provider network
deselections are unchanged. Native protected workers, sandbox, architecture,
conformance and package builds passed. Frontend passed 152 Vitest tests,
22 Node tests and its build. The independent review verified all 610 builtin
observations per Python against authored expectations and executed identities.

## Exact tested tree integrations

| Repository | Release integration | Tested source |
| --- | --- | --- |
| pine2ast | `663f042343548ea7c12d79fd01655396b89e0430` | `cae5ad7b688bad9a542d1ea100ce38d20600c99c` |
| pinelib | `4dd43f269b769c21baa859f434eea17afb74332a` | `66890b5aeec8db206fc756681a414e33d7fe5b68` |
| ast2python | `53767c031e686c6df830b8ce9cc08be1812865a3` | `70309934a05b29e1b1696109460c05db32a6f519` |
| OpenPine | `603fb836a2cca5273938b5c9b7eeb5380f1c8864` | `20e2959e0090446177f5a7d52b27e65234fb7b5c` |

Each integration has the prior release as its first parent and the tested
candidate as its second parent; its entire tree equals the tested tree. All
release references advanced without force. This is a separate documentation
publication, and source pins retain the tested source commits.

The initial scalar NA fixture adapter failure and native Windows fcntl execution
limitations remain recorded in the source and execution reviews. Expected values
were not derived from runtime observations or changed to match a failing run.

Registry cleanup is now complete: six archive tags preserve the six deleted
temporary branch histories across three repositories. The first host cleanup
failed before deletion because GitHub rejected an optional workflow-history tag.
The reviewed repair omitted only that optional tag; the permanent operations
branch retains the history. Both executions, archive identities and guarded
deletion evidence are preserved in the cleanup review. Scalar cleanup is pending.

Stage 2 remains in progress: catalog, stateful language/import matrices and full
independent builtin coverage are not yet accepted. Subsequent language, abort,
mathematical and reference work is excluded from this tested source.
TradingView verification is not claimed. Performance was not measured in this stage.
