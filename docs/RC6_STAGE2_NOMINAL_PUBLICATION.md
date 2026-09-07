# Stage 2 nominal language publication

The nominal language block passed the complete coordinated Linux gate
[34153963095](https://github.com/s7cret/openpine/actions/runs/34153963095).
The tested host is `6de3116c011ec6f6a9e437f4d9d6ee92641d5b21`, tree
`ef5b22e1088f4f38f52a6e54d9a204ccdc601bcb`. Both Python jobs and frontend checked
out this exact source; operations commit `dd82115a7d8df0c6d56a30cce6ca15710fa4f1ad`
only supplied the workflow. Every permanent backend and frontend command was
copied unchanged, with exact source/inventory checks added before execution.

| Suite | Python 3.11 | Python 3.13 |
|---|---:|---:|
| Contracts | 557 passed | 557 passed |
| PineLib | 483 passed | 483 passed |
| Pine2AST | 735 passed | 735 passed |
| Ast2Python | 684 passed | 684 passed |
| Backtest engine | 1102 passed | 1102 passed |
| Optimizer | 281 passed | 281 passed |
| Deterministic provider | 601 passed | 601 passed |
| OpenPine | 1399 passed | 1399 passed |
| **Total** | **5842 passed** | **5842 passed** |

All sixteen JUnit files contain zero failures, errors and skips. The provider's
five existing external live-network deselections remain unchanged. All 269 added
tests and all 5,573 baseline cases ran. Real AppArmor/bubblewrap workers, both
broker transports, checkpoint continuation, the frozen Stage 1 corpus,
architecture, capabilities, builds, imports and source hygiene passed.
Frontend: 152 Vitest tests, 22 Node API/package/server tests and production build
passed using this same backend's generated OpenAPI.

| Repository | Tested candidate | Release integration (same tree) |
|---|---|---|
| pine2ast | `7107e797e32c360d2fce90a6416297ef2076351e` | `c5923aeab0e914be244c5c085bb5be8b9f8ea0fb` |
| pinelib | `ad235a8d7befc091808b20927139436eb4bdbf40` | `9a6fe64657fda0b1eb7498382fdc86fa9de52205` |
| ast2python | `30512014a45db1be29def8fa8c83eb35b0d4b4c2` | `7737d0e0d903268be37bee3c0cafc6341045ae0d` |
| openpine | `6de3116c011ec6f6a9e437f4d9d6ee92641d5b21` | `b827e2e023e5d3bb2be7cc2e31abaddbe897f0e4` |

The other four component pins remain exactly those in
`docs/RC6_LIFECYCLE_SOURCES.json`. Before integration all four remote release
heads were read again and proved ancestors of their candidates. Every release
was advanced without force through a separate two-parent integration commit;
all functional, test, independent-assumption correction and documentation
commits are retained. Pine2AST's earlier integration also retains both divergent
intrabar histories. Subsequent publication documentation changes no runtime code.

Artifacts `rc6-checks-3.11-34153963095`, `rc6-checks-3.13-34153963095` and
`rc6-ui-34153963095` include XML, inventories, exact pins, source archives, build
outputs, Stage 1 receipts and frontend output. The local copies reside under
`.runtime/evidence/nominal-verify-*`; durable compact evidence is in
`verification/nominal-joint-receipt.json`.

This result replaces the Windows failures as execution evidence for this exact
candidate; the failed attempts remain described in `RC6_STAGE2_NOMINAL_TYPES.md`.
No old failing test was skipped or removed. The only changed old UDT assumptions
were independently justified and committed separately as `17c358e`.

Stage 2 remains **in_progress**, `full_stage2_accepted=false`. Complete versioned
builtin contracts/independent expected, imported methods, mixed Pine-version
execution, recursive varip reference schemas and full nominal checkpoint member
registries remain open. The builtin binding/rounding wave is separate unverified
work and is not included in this receipt. No Stage 3 work is accepted or started.

Performance was not measured in this stage.
