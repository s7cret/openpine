# Fresh audit R30-01 / R30-04 — focused fix evidence

Scope: current catalog revision binding and exact smoke admission only. This is
not stabilization, full installed-package, performance, or language acceptance.

## R30-01

The unchanged mandatory resource-tamper test first failed on actual Python
3.11.15 and 3.13.5 at its lifecycle/catalog ref equality assertion. The current
pin was regenerated from Git object
`6dfd47badff5ef5b038dfd48a9fb3d8e762b1836`, using `git show` for every
`pine2ast/catalog_data/packs/pine_v{1..6}.pack.json`: each pack seal was verified
and its raw resource SHA-256 computed independently of runtime observations.
All six raw/content identities equal the previously reviewed pack identities;
only the current source revision and current pin seal changed.

Current pin seal:
`sha256:da06459d4251a65bd3490828df4e6f33eec53325eacb04c0817f313d82c76d6f`.
The resource reader now compares the pin revision with the independently packaged
coordinated stack lock, rejecting resealed wrong/stale revisions even when the
pack bytes are unchanged. New negative tests retain missing-version and broken
seal rejection. No historical archive authority, oracle expected, or receipt was
rewritten. Installed CI preparation invokes the same reader before collection
and the long component campaign.

Real host wheel/sdist builds on both interpreters contain byte-identical current
pin resources through the existing setuptools package-data declaration. Each
wheel was extracted to an isolated target and its packaged preflight exercised
under `python -I`, with no source path or PYTHONPATH fallback. This is a host
resource-inclusion check, not acceptance of all normal/rebuilt stack installs.

## R30-04

A real collected parameterized smoke ID failed admission before the fix. Six
unsafe frozen file selectors also failed the new rejection assertions before
file-selector validation was strengthened. Safe file selectors and exact frozen
collection membership now determine admission; parameter identities are opaque
literal bytes, not patterns. Eight real plain/parameter cases (Unicode, `*`, `?`,
brackets, slash/parent-like text, option-like text, escaped backslash) each produce
one planned node and execute one test through argv-list subprocess invocation.
Unknown IDs, duplicates, unsafe paths, controls, and standalone pytest options
remain rejected. Existing affected selection and preparation-closure tests pass;
no prerequisites were reintroduced into the affected test denominator.

## Focused execution and inventory

The four-file focused pack (`tests/test_stack_lock.py`, execution-platform,
execution-CI, and stage2-audit mutations) passed **143 tests on each** actual
interpreter. The existing installed API wheel test needed explicit
`OPENPINE_SOURCE_ROOTS`: sibling checkouts are not all present in the integration
directory. Reviewed buildable sibling roots were resolved and their package-tree
hashes checked against the lock; no synthetic roots or shared sibling PYTHONPATH
were supplied. Its first missing-root failure remains a prerequisite failure,
not a product regression.

Full host **collection only** on 3.11/3.13 produced identical 11,167-node
inventories, with zero deselections. Exactly 22 new cases extend the existing
11,145 inventory. Removing these cases reproduces the prior inventory and the
unchanged historical 11,027-node baseline through `validate_inventory`.
`verification/rc6-r30-01-04-inventory-delta.json` records the exact additions and
hashes. Other component locks and their declared network deselections are
unchanged. The host package-tree lock was refreshed through
`openpine.stack_lock.package_tree_identity` before builds.

Local raw evidence (not portable full acceptance receipts):

- `/home/moltbot1/.hermes/cache/scratch/rc6-r30-final{311,313}.log`
- `/home/moltbot1/.hermes/cache/scratch/rc6-r30-final{311,313}.xml`
- `/home/moltbot1/.hermes/cache/scratch/rc6-r30-collect{311,313}.txt`
- `/home/moltbot1/.hermes/cache/scratch/rc6-r30-resource-builds/{311,313}/`

R30-02/R30-03 implementation, final R30-05 mapping/current-view review, and
R30-06/R30-07 product qualification remain separate obligations. PR21 remains
draft and unmerged; no service, frozen attempt, or release branch was changed.
