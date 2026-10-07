# INT05 hosted control review — dispatch remains blocked

The saved container cannot expose per-thread procfs children. Its mandatory
optimizer gate is unchanged. This delta adds an opt-in manual job to the existing
`rc6-preparation.yml` Ubuntu 24.04 VM workflow, reusing its pinned actions and
dedicated protected-worker provisioning. Existing baseline steps remain intact.

The workflow adapter has its own commit **W**. It checks out W under
`workflow-owner/`, and product commit **C =
4eee24fc458a2099f000211850f720ccfc6bfb2b** under `candidate/`.
`reviewed_workflow_sha` must equal W; the INT05 mode defaults to false. There are
no push or PR triggers for this review branch. Do not merge or dispatch it during
review. Product sources, seven library pins, reserved owners and schemas are
unchanged by the adapter.

The sequence is one stock `test-ci prepare`, one stock offline
`test-ci owner-environment` restore, fresh owner launch preparation, one
`make_ci_plan` refreeze and one complete `run_campaign` / `aggregate_campaign`
control. The resource wrapper uses the existing declared-process and observation
owners, with per-stage disk admission and shared deadline cancellation. There are
no mutant runs or automatic retries.

The prepared/restored environment must match exactly. Source inputs must equal
the independently observed product source hash
`sha256:725720c3dfedf6818b1bb3ddb06657643ccf2853bcce5692833b6bd74b5cad8a`.
The hosted bundle, collection, interpreter/distribution identity, owner tools,
binding and full plan are newly frozen. `refreeze.json` records actual counts and
node ID hashes; it never imports or relabels any old 9e/source-venv campaign anchor.

This calibration executes the full pytest inventory of all eight owners. The
unchanged stage-full plan retains branch-reconciliation, foundation,
protected-workers, coverage, frontend, packages and test-performance gates.
Its aggregate continues to report those gates separately. A green pytest scope
does not set stage, release, provider-live, performance or spec acceptance.
Provider five live obligations and the remaining 68-requirement spec stay open.

## Runtime and bounded allocation proposed for review

* Standard public-repository `ubuntu-24.04` VM, ordinary CPython **3.13.5** from
  the existing official setup-python action; GIL enabled, ensurepip required.
* PID 1 must be systemd; own per-thread procfs children must be readable.
  The unchanged optimizer gate subsequently checks pidfd and subreaper support.
  Existing AppArmor, bubblewrap, passwordless sudo and `openpine-worker` recipe
  must pass. No container workaround or gate bypass is included.
* One job, **120-minute** ceiling. Its first step sets shared work and retention
  deadlines at minutes **100** and **110**, leaving a planned 10-minute upload
  margin. The monitor refuses late admission and interrupts its declared child
  on deadline, with a 30-second cleanup grace. This is not a measured full
  duration or a guarantee that `always()` runs after runner loss, forced
  cancellation or the platform's hard job timeout.
  Existing Node 24 / npm lock / Playwright Chromium preparation supplies actual
  executable locators without executing frontend acceptance.
* Four CPUs / 16 GB RAM runner profile; campaign jobs **2**, parallel shards **1**,
  one serial component window. **6144 MiB** applies to the existing campaign's
  sampled RSS/reservation enforcement. Other stages record RSS without claiming
  that memory ceiling or a kernel hard limit; VM memory exhaustion remains a
  possible infrastructure failure.
* Actions reads actual free disk and the unchanged product policy floor
  (**2 GiB**). The cloud's 18/20 GiB coordination reserve is not carried into
  this new hosted plan. Additional stage allocations: bootstrap 0.5 GiB,
  prepare 2 GiB, restore 1.25 GiB, npm 0.5 GiB, Chromium 1 GiB, refreeze 64 MiB,
  control 2 GiB, retention twice the artifact cap plus 16 MiB. The monitor
  requires floor plus allocation before launch, cancels on sampled global
  growth above it, and keeps a 64 MiB floor margin. These are allocations to
  test fit, not observed successful peaks. Provisioning and setup-node have
  5-minute timeouts; their disk/RSS use is outside the wrapper and is checked
  by the next admission. GitHub documents 14 GB SSD; no free capacity is inferred
  from the label. No old 21-manifest floor is edited. Failure to fit stops work;
  there is no OS cleanup or paid runner fallback.
* The artifact cap remains **128 MiB** without an approved increase. It bounds
  both uncompressed and compressed archive bytes; the complete upload including
  manifest, audit and ZIP reserve must fit too. Retention is exactly **1 day**.
  Account free artifact allowance must be established before dispatch. Public
  standard compute is free; artifact storage has separate allowances. This delta
  does not authorize any storage overage or change account billing settings.

Actual partial local stock prepare took 95.3685 seconds through 39 successful
commands, built eight real wheels, sampled 254.83 MiB peak RSS, consumed 1.1412 GiB
of global free disk and retained 1.0970 GiB. It then failed the mandatory procfs
gate before sealing a bundle or collecting tests. These are partial observations,
not estimates presented as successful preparation or control cost.

## Publication owner boundary that must be resolved before dispatch

`retain` writes a complete original-byte inventory and a clearly marked partial
preflight public audit before checking raw size. Oversize scope leaves originals
untouched and records `retention-failure.json` before copying or archiving. When
the scope fits, the complete public audit is written before creating the archive.
Archive and complete upload failures retain explicit failure checkpoints too.

The declared input scope includes `host.tar`, `sources.tar.gz`,
all eight Git bundles and wheelhouse, plus identities, collection receipts,
plans, phases, JUnit, command logs and resource observations. Existing
`archive_evidence` seals and reads back original bytes, including failures.
Only explicit private trees may be excluded, and its existing descriptor check
rejects exclusion of a primary. No required primary is dropped to fit a budget.

The existing `scripts/rc6_public_evidence.py` owner only permits bounded text
preparation receipts. It rejects source archives, Git bundles and expanded
campaign scope. Its current audit does **not** inspect contents of those rejected
archives; this adapter does not claim a nested secret/PII scan. The tested
negative boundary sets `public_ready=false` and withholds all public upload.

Consequently this delta is **not ready for dispatch**. Archives held only on an
ephemeral runner would not provide durable retention after the job ends. Before
dispatch, the publication/workflow owner must supply a reviewed durable route
that preserves the complete inputs and performs the requested secrets/PII check
over source/provenance and primary bytes, within confirmed free storage allowance.
This change does not expand the public allowlist, redact originals, silently
allow Git author PII or discard archive findings. Review this boundary together
with W, runtime requirements and the one-control budget.

`execution_binding.checked_locations` can rehash exact public source files
reconstructed at new locations, without changing historical execution provenance.
It does not change prepared transport: `verify_bundle` requires every original
file and its exact hash. No source-SHA substitute is applied to that owner.
`aggregate_campaign` requires raw `.coverage` and every descriptor-bound owner
primary; derived coverage JSON or sanitized stdout cannot replace them.

The smallest route proposed for owner review is the already-authorized public
technical projection under the unchanged 128 MiB/1-day cap, plus a separately
approved **free private durable destination** for originals that cannot be
published. A private archive must not be uploaded as a public Actions artifact.
The destination, enforceable byte ceiling and factual free allowance are unknown,
so this remains a proposal. Retention's own final monitor receipt is produced
after its archive operation and requires that durable collector too; it is not
claimed to be inside the archive it measured. No public scope expansion, budget
increase, reconstruction substitution or private upload is implemented.

References: [standard runner resources](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[Actions compute and storage billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
