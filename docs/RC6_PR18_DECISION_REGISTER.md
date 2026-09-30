# PR #18 continuation decision register (RC6)

Compare `501e5df5eb05ddd189f87d7aafc56a024415b543..07825e876ac2cfcecd9907ad550669ed5d12fd70` to current release base `faa62e08eb08a79723d94b881ba0588db27985a9`. This is the unique PR patch (not the misleading two-endpoint diff that includes newer release additions). No branch is merged or deleted.

Paths examined: **130**; `already_ported` 6, `port_required` 23, `rejected_with_reason` 96, `superseded_with_mapping` 5.

`already_ported` includes changes implemented in this uncommitted stabilization worktree where noted; it does not claim they are in release. `port_required` is an outstanding decision/implementation, **not** approval to copy historical files. Stage 2 language acceptance remains separate and false. Old Stage 2 receipts retain their original SHA/date in PR history; they cannot be current receipts.

Evidence: the release source/test functions, PR #18 source diff and exact bytes, `verification/inventory.json`, and the pinned pine2ast copy were examined. The mixed-version linker is byte-different and contains `_admit_version_invariant_unit` only in PR #18; this requires a semantic owner review before deciding whether/how to port.

## already_ported (6)

- `openpine/verification/__main__.py` — stage2-1 catalog/lock CLI exists in release; this branch preflights output placement.
- `openpine/verification/pytest_gate.py` — reviewed_addition_to_hashed_baseline validation exists in release pytest_gate.validate_inventory.
- `rc6_tests/test_rc6_inputs.py` — falsey and input.source assertions ported into same two existing node IDs in this branch; 5 targeted cases passed.
- `rc6_tests/test_rc6_stage21_catalog.py` — all archived test function names exist in release; two additional fixture-isolation cases exist.
- `rc6_tests/test_rc6_stage2_audit_gate_mutations.py` — all archived test function names exist in release; resource-pin tamper regression added.
- `verification/stage2-1-authority.json` — byte-identical to release file.

## superseded_with_mapping (5)

- `openpine/verification/stage2_catalog.py` — catalog owner and resource-pin adapter in current release; archived _source_files replaced by shared source_snapshot.v2 in this branch.
- `verification/inventory.json` — PR count 9723; current reviewed-addition inventory count 11064, baseline 11027 retained.
- `verification/stage2-callable-lock.json` — both count 2390 but different sealed contents; current reviewed lock retained; reconcile changed row identities in language continuation.
- `verification/stage2-remaining-matrix-lock.json` — current hash belongs to current remaining matrix, not 18 Sep matrix.
- `verification/stage2-remaining-matrix.json` — current 16-item matrix and sealed lock retained; old count alone is not equality.

## port_required (23)

- `stage2-continuation/continuation-work/completion-checks/test_exact_runner.py` — review exact-runner counterexamples against native execution and current source pins; old harness not copied.
- `stage2-continuation/continuation-work/completion-checks/test_version_invariance.py` — review version-invariance counterexamples in pine2ast owner; old harness not copied.
- `stage2-continuation/continuation-work/sources/pine2ast/pine2ast/libraries/linker.py` — PR mixed-version admission _admit_version_invariant_unit absent in pinned pine2ast; owner semantic review needed; do not overwrite newer linker.
- `stage2-continuation/continuation-work/sources/pine2ast/pine2ast/libraries/version_compatibility.py` — pinned version is byte-different; review any missing historical invariance without overlay.
- `stage2-continuation/continuation-work/sources/pine2ast/tests/stage2/test_stage2_import_version_matrix.py` — pinned tests have four extra version cases; PR unique mixed-version rejection test needs semantic mapping.
- `verification/stage2-1-callable-lock-delta.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-1-version-exact-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-10-language-publication.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-2-version-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-3-cumulative-catalog-delta.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-3-reaudit-delta.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-3-state-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-3-upstream-catalog-regression.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-4-acceptance.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-4-complete-inventory-20260917.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-5-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-6-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-7-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-8-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-9-matrix.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-audit-callable-review.json` — historical Stage 2 authority/delta/matrix must be mapped by language owner before any current use; never copy as fresh acceptance.
- `verification/stage2-current-acceptance.json` — current verdict contract is required but 18 Sep values cannot be reused; FIX-03.
- `verification/stage2-progress.json` — migrate canonical current status/readers without overwriting dated historical receipts; FIX-03.

## rejected_with_reason (96)

- `docs/RC6_STAGE2_10_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_1_FINAL_ACCEPTANCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_1_LOCAL_ACCEPTANCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_1_POST_AUDIT.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_1_REAUDIT.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_2_ACCEPTANCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_3_ACCEPTANCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_3_REAUDIT.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_3_REAUDIT_V2.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_3_REAUDIT_V3.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_4_REFERENCE_LIFECYCLE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_5_UDT_ENUM.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_6_METHODS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_7_CONTROL.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_8_IMPORTS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_9_BUILTIN_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_ABORT_CHECKPOINT.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_ACCEPTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_ARRAY_CONCAT_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_ARRAY_OPERATIONS_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_BUILTIN_EVIDENCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_BUILTIN_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_CANDIDATE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_CONCAT_VARIADIC_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_CONSUMER_LIBRARIES.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_CONSUMER_LOCAL_RESULT.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_EVENT_HISTORY_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_EXPLICIT_METHOD_CALLS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_EXPLICIT_METHOD_PUBLICATION_LOCAL.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_FUNCTION_OVERLOADS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_FUNCTION_OVERLOADS_LOCAL.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_INTRABAR_COLLECTIONS_LOCAL.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_LANGUAGE_CORE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_LANGUAGE_STATE_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_LIBRARY_METHODS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_LOCKED_IMPORTS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_LOCKED_IMPORTS_LOCAL_RECEIPT.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_MAP_OPERATIONS_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_MATRIX_OPERATIONS_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_METHOD_CONTEXT_CONTRACTS_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_METHOD_IDENTITY.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_MIXED_CALLABLE_FAMILIES.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_MIXED_CALLABLE_PUBLICATION_LOCAL.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_NOMINAL_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_NOMINAL_REGISTRY.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_NOMINAL_TYPES.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_NUMERIC_INDEX.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_NUMERIC_INDEX_LOCAL_RESULTS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_QUALIFIER_BOUNDARY.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_QUALIFIER_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_RECURSIVE_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_RECURSIVE_TA_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_REFERENCE_LOOP_VALUES.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_REGISTRY_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_ROLLING_CONTEXT_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_ROLLING_STATISTICS.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_SCALAR_EVIDENCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_SCALAR_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_STRING_OPERATIONS_EXPECTED.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_TRANSCENDENTAL_EVIDENCE.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `docs/RC6_STAGE2_VALUES_PUBLICATION.md` — dated 18 Sep stage-2 acceptance/publication note; not a current RC6 verdict; retained in PR Git history.
- `rc6_tests/test_rc6_scalar_availability.py` — replacing independent fixed 2390 denominator with len(lock.rows) weakens oracle; current fixed count and reviewed lock retained.
- `stage2-continuation/CONTINUATION_STATUS.md` — 18 Sep continuation status/instructions superseded; keep historical Git record.
- `stage2-continuation/PACK_README.md` — 18 Sep continuation status/instructions superseded; keep historical Git record.
- `stage2-continuation/REPAIR_REPORT.md` — 18 Sep continuation status/instructions superseded; keep historical Git record.
- `stage2-continuation/continuation-work/build_stage2_package.py` — experimental parallel packaging/runner workflow must not replace current verification execution owner; retained as reference.
- `stage2-continuation/continuation-work/completion-checks/workflow-template/stage2-exact-candidate.yml` — experimental parallel packaging/runner workflow must not replace current verification execution owner; retained as reference.
- `stage2-continuation/continuation-work/completion-evidence/mixed-version-integration.json` — old source/interpreter evidence cannot be relabeled for current eight-component candidate.
- `stage2-continuation/continuation-work/completion-evidence/mixed-version-runtime.json` — old source/interpreter evidence cannot be relabeled for current eight-component candidate.
- `stage2-continuation/continuation-work/completion-evidence/preflight/report.json` — old source/interpreter evidence cannot be relabeled for current eight-component candidate.
- `stage2-continuation/continuation-work/completion-evidence/preflight/source-snapshot.json` — old source/interpreter evidence cannot be relabeled for current eight-component candidate.
- `stage2-continuation/continuation-work/previous-packaging-result.json` — old source/interpreter evidence cannot be relabeled for current eight-component candidate.
- `stage2-continuation/continuation-work/run_exact_stage2.py` — experimental parallel packaging/runner workflow must not replace current verification execution owner; retained as reference.
- `verification/stage2-1-final-test-results.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-inventory-rebaseline-review.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-local-acceptance.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-post-audit-environment.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-post-audit-test-results.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-reaudit-test-results.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-source-lock.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-1-source-reconciliation.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-2-inventory-review.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-2-local-acceptance.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-2-source-lock.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-2-test-results.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-3-inventory-review.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-3-local-acceptance.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-3-source-lock.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-3-test-results.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-3-third-reaudit.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-4-inventory-review.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-4-source-lock.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-4-test-results.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-audit-inventory-review.json` — dated pre-RC6 candidate receipt/rebaseline; preserve in PR history, not a current acceptance/source lock.
- `verification/stage2-source-lock.json` — 18 Sep source lock is stale for current eight-component candidate.

## Gate decision

PR #18 is **not safe to merge wholesale**. Its host inventory is 9,723 while the release inventory is 11,064, it removes the newer execution platform in the endpoint diff, and its `stage2-current-acceptance.json` is dated 18 September. Keep the PR open and its branch intact while `port_required` items are settled by their owners; no current acceptance or fresh receipts are inferred from this register.
