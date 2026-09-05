# RC6 cleanup — 2026-09-05

This pass removes environment-dependent Parquet substitutes and completed one-shot
maintenance code. It does not delete production data, migration SQL, support for
Pine v1-v6, or archived branch history.

## Retired

- `openpine/_compat`: JSON/pickle Parquet fallback and the fake structlog logger removed.
  Production logging now uses the required structlog package and preserves bound context.
  All storage/export callers now use `openpine.storage.parquet` and actual PyArrow.
- The apply-reviewed-series workflow and encoded-series extension helper, which
  targeted an already retired maintenance branch.
- The one-shot branch-consolidation script and its dedicated tests. The completed
  receipt, source mapping, original script/tests and every retired tip remain
  reachable from `ops/rc6-delivery-branch-selection` and the previous RC6 commits.
- Historical RC2/RC4 candidate templates and the 4.0 release checklist. Historical
  releases have their own retained branches; RC6 has one active candidate template.

## Storage compatibility

PyArrow is already a required package dependency. A `.parquet` output now always
contains Parquet; missing dependencies fail at import rather than silently writing
a different format. Real Parquet written by earlier releases remains readable.
Existing JSON or pickle files with a `.parquet` extension are rejected, including
when the old `OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET` environment variable is set.
No automatic unpickling or destructive conversion of old data is performed.
Reimport those datasets from their original source using the normal data path.

Writes use a same-directory temporary file and replace the destination only after
a successful write and file sync. Failed writes preserve the existing file and
remove their temporary output. Row counts and lake health read only file metadata.
This does not claim a filesystem-wide transactional or crash-durability guarantee.

## Deliberately retained

SQLite migrations and schema normalization are needed to open existing user databases.
`legacy_4x` is an explicit broker semantic profile, not Pine v1-v4 language support;
removing it indiscriminately would change stored strategies. Working provider/CLI
adapters are not dead code merely because their names predate RC6. Their replacement
requires caller migration and conformance evidence, not a substring-based deletion.
