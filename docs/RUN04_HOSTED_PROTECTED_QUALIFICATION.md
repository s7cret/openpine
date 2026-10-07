This branch prepares a reviewable INT04/INT05 hosted attempt. It has not run
the hosted fault matrix and does not claim full product qualification.
The base is Host PR #40, commit `89494613ba34d50c520e87e17a977b80754a6b29`.
Reviewed owner changes are from frozen INT04
`aea48bec52b609a9516f0d589ae7c47d6a95853a` and INT05
`0bbcfc08206efa8325a6148390fe228785247e5d`.
Their original worktrees and primary evidence remain unchanged.

The exact seven producer commits are in both `docs/RC6_LIFECYCLE_SOURCES.json`
and the candidate template. The action injects its approved Host commit,
builds the existing eight wheels, seals a wheel-bound manifest and restores two
independent installed placements. All eight product imports must originate
inside each restored prefix. Tests are exposed through their three explicit
namespaces, never by adding a product source parent to `sys.path`. INT05's
original nested source-mutation oracles retain their intentional source scope;
they are sensitivity evidence, not installed product acceptance.

Each A/B placement runs interactive and bulk backtest with three actual faults:
the existing `run_logged` timeout, SIGINT to the retained fixture coordinator,
and SIGKILL to the retained controller. Every fault has a concurrently live
independent neighbour. The original compiled native-array strategy and its
six-bar, intent and trade assertions are retained. Instrumentation records
the production UUID allocation, then holds after validated HELLO and the
original LOAD_ARTIFACT/INIT_RUN writes. Its bulk idle budget is 240 seconds
so the 60-second supervisor timeout, rather than this diagnostic hold, causes
the requested fault. It substitutes the actual candidate admission identity
for the original synthetic test admission only in this fault fixture.

The harness queries the real exact UUID systemd unit and bounded cgroup
membership, retains worker/controller pidfds before signalling, and checks
the actual fault exit and guardian primary. An unavailable manager cannot
prove absence. The neighbour must remain in the same unit/cgroup and then
complete the original six-bar assertions. Automatic results are published
before forced disposal. Disposal cannot rewrite those results or make a
failed automatic cleanup pass; persistence failure also fails the attempt
while owned disposal continues. Allocated UUID observations cover setup
failure without scanning or stopping unrelated units.

The action also executes the original 49-case INT04 file, the locked 76-case
INT05 owner contracts, and all 227 explicitly selected protected-worker
obligations on each placement. The public fault projection reports only the
12 matrix cases. Passing these checks does not execute the separate complete
affected/full comparison campaigns described in `INT05_QUALIFICATION.md`.

Inventory review keeps every historical node identity and base collection
hash: Host 11,998 → 12,197 (+199), Contracts 557 → 619 (+62), Core 1,241 →
1,488 (+247), PineLib 5,370 → 5,394 (+24). Historical sources were recollected
at Host `f02dfb64eae8dd06221d207dc10f8043469265c6`, Contracts
`db1745756516b47466756c9c5d38fbbb95595a3b`, Core
`d9210fbb6a72689e76da918c2eba422b22f439f6`, and PineLib
`b953e803d618a9784b117f8047ca709791aaca79`, and validated against the existing
locks before computing additions. No test IDs were removed. The established
Provider `not live_network` marker deselects five cases; its stale zero was
corrected to five without changing inventory identities, count or hash.

The existing workflow has a separate protected job. Ordinary implementation
branch pushes and PRs do not start it. The workflow is absent on `main`, so
the immediately reviewable launch is creation of a fresh dedicated execution
ref from the final approved SHA:

```sh
git push origin FINAL_APPROVED_SHA:refs/heads/execution/run04-int04-int05-20261007
```

Creating that ref is an execution action and requires separate action-time
approval of the exact SHA, security provisioning, public projection and raw
retention limitation. The ref must first be confirmed absent; no force push
or ref reuse is authorized. Dispatch support is available once this workflow
exists on the repository's default branch; it is not assumed available now.
The workflow checks source/workflow/approved SHA equality before provisioning.

The execution would use one standard public Ubuntu 24.04 VM, at most 4 CPU,
16 GiB RAM, 90 minutes, a 3,000,000,000-byte disk reserve, and no cache upload.
Its security actions are exactly: apt update/install of AppArmor tools/profile
and bubblewrap; install/load `bwrap-userns-restrict`; create the absent system
nonlogin `openpine-worker` user; UID/bubblewrap probes; existing protected
UUID unit creation, manager observation and exact owned-unit disposal. There
is no sudoers, sysctl or global cgroup change. Wheel/dependency preparation
uses the existing hashed dependency locks and seven pinned GitHub sources.

The new public policy is
`verification/protected-qualification-public-allowlist.json`. Only
`projection.json` and `projection.sha256` may be uploaded to public
`s7cret/openpine`. Permitted categories are candidate/source commit identities,
policy hash, enum matrix case identities and boolean outcomes. There are no
raw logs, private paths, process/unit identifiers, environment, protocol,
traces, sources, wheels, virtualenvs or old workspace evidence. Metadata is
limited to 1 MiB, with a separate 64 MiB complete-upload ceiling and one-day
retention. The old preparation 128 MiB/$0.10 permission is not reused.
Proposed new storage ceiling is $0.01 for this attempt; account quota is
unknown. Standard public-runner compute is free under the published GitHub
pricing; the 64 MiB one-day maximum storage estimate is below $0.0006 at
$0.25/GiB-month. These are estimates, not a configured billing guard.

Raw primaries and detailed driver traces stay in the ephemeral VM's private
paths. No private durable endpoint, readers or quota is approved. Explicit
ephemeral acknowledgement permits execution but does not close that archive
obligation. Both `raw_primaries_durable` and `full_qualification_accepted`
remain false in the projection even if every matrix case passes.

Sources: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
and https://docs.github.com/en/billing/concepts/product-billing/github-actions.
Artifact readers: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts.
