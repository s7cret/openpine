# RC6 evidence storage and private retention

The existing `openpine.verification` evidence owner can archive a whole scoped
run at a CI boundary. This is a transport/integrity check; an archive of a
failed or blocked run remains failed or blocked. It never grants product,
Stage 1, or release acceptance.

The active `verification/execution-policy.json` reserves **3,000,000,000 bytes**
of available space (3 GB decimal). New guarded campaign plans freeze this value
and sample the evidence/output filesystem before work and during execution.
Below the floor, or when observation fails, the existing owner refuses/cancels
its work and preserves diagnostics. Equality is allowed. Local RUN04 probes
use the same task floor; ordinary shell/editor work has no global disk guard.
The previous 20 GB value was a parent task resource limit recorded in prepared
INT04 inputs, separate from the policy's earlier 2 GiB minimum. The user replaced
that task limit with 3 GB. Historical plans and receipts retain their original
values. This disk limit does not grant storage sharing or qualification approval.

Keep code, policies, baseline source pins and small reviewed manifests in Git.
Keep full command logs, JUnit, coverage, owner receipts and archives in external
run directories and CI artifacts. Previous attempts and original failures are
immutable. Use a new output directory for each attempt.

## Create and verify

Run with the existing prepared CPython 3.13 environment. Evidence and archive
output must be outside every source root, and outside each other's tree. Supply
all eight source roots in the real stack, even though the short example below
only shows two. The candidate is the exact host Git SHA or the sealed source
snapshot hash. `--source-hash`, when available, binds the archive to the complete
source snapshot used by the existing planner.

```bash
python -m openpine.verification.execution_evidence archive \
  --evidence /external/run-17/campaign \
  --output /external/run-17/transport \
  --source-root /stack/openpine --source-root /stack/pinelib \
  --candidate-sha "$CANDIDATE_SHA" --source-hash "$SOURCE_HASH" \
  --run-id "$RUN_ID" --retention-days 1 --max-bytes 268435456

python -m openpine.verification.execution_evidence verify \
  --archive /download/transport/evidence.tar.gz \
  --manifest /download/transport/manifest.json \
  --expected-manifest-hash "$MANIFEST_HASH" \
  --candidate-sha "$CANDIDATE_SHA" --source-hash "$SOURCE_HASH" \
  --run-id "$RUN_ID"
```

Creation prints the sealed manifest hash for the caller's small run metadata.
Record that hash, run ID and candidate independently in the CI job outputs or
reviewed manifest. Download verification must use those expected identities;
reading the expected hash from the same downloaded manifest only verifies
self-consistency. The verifier reads all compressed bytes and every regular
member, checks gzip completion, complete inventory, sizes, SHA-256 and identity,
and never extracts archive paths. Missing, truncated, changed, duplicate,
foreign, traversing or symlink content is rejected.

`evidence.tar.gz` is deterministic for the same inventory and has normalized
metadata. `manifest.json` records per-file hashes, primary byte count, archive
byte count, candidate/source/run identities, declared exclusions and retention.
Creation writes once, verifies archive and manifest readback, and rechecks the
original inventory. A failed write preserves its owned `.partial` diagnostics;
the next attempt uses a new output directory. Originals remain in place.

`--max-bytes` is required and bounds both total primary bytes and compressed
archive bytes. Creation also reserves 16 MiB of free disk beyond its estimated
archive demand. Archive copies and downloads still consume extra disk; account
for each copy in the existing runner preflight rather than deleting history.
Neither this archive nor a CI upload receipt substitutes for the owner's
primary evidence checks.

## Declared temporary files

Archive every file by default. An explicit `--private task@py/s000/a001/private`
can exclude a known owner temporary tree; arbitrary directory names, overlapping
exclusions and references to primary artifacts inside an excluded tree are
rejected. Interior pytest symlinks can stay inside an excluded private tree.
Exclusion does not delete that tree. Primary stdout/stderr, receipts, JUnit and
coverage must always remain in the archive. Failed attempts keep their private
trees and original logs; an unavailable or corrupt archive never authorizes
cleanup.

The existing campaign's `delete-on-success` policy and guarded cleanup owner
remain unchanged. For a caller that defers cleanup until archive verification,
the explicit `cleanup-private` command additionally authenticates the archive,
the full source identity, the exact successful attempt and all primary
references before invoking that same owner:

```bash
python -m openpine.verification.execution_evidence cleanup-private \
  --plan /external/run-17/plan.json --evidence /external/run-17/campaign \
  --attempt 'openpine@py313/s000/a001' \
  --archive /download/transport/evidence.tar.gz \
  --manifest /download/transport/manifest.json \
  --expected-manifest-hash "$MANIFEST_HASH" \
  --candidate-sha "$CANDIDATE_SHA" --run-id "$RUN_ID"
```

The frozen task must explicitly declare `delete-on-success`. Cleanup removes
only its guarded `private` directory. Source trees, primary files, adjacent runs,
archives, and failed/timeout/cancelled attempts remain untouched. There is no
general cleanup command and no invocation of `release_gate.sh`.
The archive's inventory paths must be relative to the same evidence root
supplied to cleanup; a broader archive from another scope is rejected.

## CI artifacts and expiry

Set artifact retention explicitly to 1 or 7 days according to the agreed run
storage budget; the helper accepts 1–90 days and has no implicit retention.
Use the same retention value in the manifest and `actions/upload-artifact`.
Archive unsuccessful jobs too, and run the verifier after downloading before
aggregation or any opted-in private cleanup. Store the small run IDs, archive
hashes, upload identities and expiry policy separately from heavy logs.

Check repository visibility and the account's artifact storage applicability
before a hosted upload. Public standard-runner compute eligibility does not
establish a free artifact storage allowance. If that allowance cannot be
verified, preserve local evidence and report the blocked upload rather than
starting unbudgeted storage use. Retention expiry is not a permanent evidence
archive: export and verify any evidence needed beyond the CI retention window.

The targeted regressions are `rc6_tests/test_rc6_evidence_archive.py` and the
existing `rc6_tests/test_rc6_private_retention.py`. They include real tiny gated
pytest attempts, successful cleanup, failure preservation, path and source
guards, missing/corrupt downloads, foreign identities and archive byte budgets.

## Public preparation transport

The bounded preparation workflow publishes only the explicit allowlist in
`scripts/rc6_public_evidence.py`: JUnit, text coverage, SHA/version manifests,
complete preparation command logs and collection receipts. Public upload requires
user approval and a cost-safe storage route. `scripts/rc6_public_evidence.py`
checks UTF-8 bytes, credentials, private keys, tokens and personal email addresses,
and rejects symlinks, databases, source archives, unknown paths and oversized data.
It records hashes without printing sensitive values or changing primary bytes.
A sensitive-content failure blocks upload and qualification; it is never a PASS.

The transport retains all preparation primary logs and raw collection receipts;
source archives, wheel build inputs, virtualenvs and caches stay outside the public
transport. This bounded baseline does not substitute for the full package/foundation
owner evidence. No full product qualification is granted by this archive.
The preparation workflow defaults to a zero artifact budget until the free storage
route or a storage ceiling has been confirmed. An ephemeral baseline must remain
blocked while its required primaries cannot be retained.
