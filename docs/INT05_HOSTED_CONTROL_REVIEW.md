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
control. The resource wrapper is the previous stock-command measurement recipe,
with only a lint annotation added. There are no mutant runs or automatic retries.

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
* One job, **120-minute** ceiling: planning allocation 35 minutes prepare,
  75 minutes control, 10 minutes retention. This is not a measured full duration.
  Existing Node 24 / npm lock / Playwright Chromium preparation supplies actual
  executable locators without executing frontend acceptance.
* Four CPUs / 16 GB RAM runner profile; campaign jobs **2**, parallel shards **1**,
  one serial component window, campaign memory **6144 MiB**.
* Initial measured free disk must be at least **20 GiB**. Prepare/restore use
  hard **18 GiB**, soft **20 GiB**, sampled 64 MiB cancellation margin; the fresh
  control plan freezes 18 GiB. No existing 21-manifest floor is edited.
  GitHub documents only 14 GB SSD for standard runners, so adequate measured
  free disk is an admission requirement, not a guaranteed property of the label.
  Insufficient capacity stops the attempt; no OS cleanup or paid runner fallback.
* Proposed complete artifact ceiling: **512 MiB**, including archive, manifest,
  audit and existing conservative ZIP reserve. The default input remains 128 MiB;
  the review must choose the appropriate ceiling. Retention is exactly **1 day**.
  Account free artifact allowance must be established before dispatch. Public
  standard compute is free; artifact storage has separate allowances. This delta
  does not authorize any storage overage or change account billing settings.

Actual partial local stock prepare took 95.3685 seconds through 39 successful
commands, built eight real wheels, sampled 254.83 MiB peak RSS, consumed 1.1412 GiB
of global free disk and retained 1.0970 GiB. It then failed the mandatory procfs
gate before sealing a bundle or collecting tests. These are partial observations,
not estimates presented as successful preparation or control cost.

## Publication owner boundary that must be resolved before dispatch

`retain` copies complete prepared inputs, including `host.tar`, `sources.tar.gz`,
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

References: [standard runner resources](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[Actions compute and storage billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
