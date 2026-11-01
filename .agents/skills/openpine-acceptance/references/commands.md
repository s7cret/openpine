# R6 command map

Run from the Git root. In the examples, `python` means a resolved interpreter
already checked to be Python **3.14.8**. In PowerShell use
`& $r6Python ...` when its absolute executable path contains spaces.
Do not change dependencies or select another patch version just to run a gate.

## Confirm inputs

```text
git rev-parse --show-toplevel
git branch --show-current
git status --short
python -c "import sys; assert sys.version_info[:3] == (3,14,8); print(sys.executable)"
python -m tools.release_gate --help
```

Capture the content identity with the existing function, not `git rev-parse`
alone. Save the complete value to the task proof directory when running a gate:

```python
import json
from pathlib import Path
from tools.ci_artifacts import source_identity
value = source_identity()
print(json.dumps(value, ensure_ascii=False, indent=2))
# If needed: Path(task_proof, "source-before.json").write_text(...)
```

The fingerprint includes uncommitted application inputs and dependencies;
a Git commit does not establish that its checkout is the same source.
`source_identity()` excludes `conformance/results/`, docs, `.agents/` and
`AGENTS.md`. Review those files separately. Documentation is included in newly
built wheels even though it is outside this fingerprint: reusing an older wheel
proves its application payload, not that it includes later documentation.

## Proportionate checks

```text
python -m tools.verify_architecture
python -m tools.verify_spec
python -m tools.test_runner --module test_array_slice.py --output .runtime/task-proof/array-slice-tests.json
python -m tools.release_gate --fast --timings .runtime/task-proof/feedback
```

Choose actual affected module names from `tests/`; `test_array_slice.py` is an
example. Fast feedback is neither full native qualification nor installed proof.

For a changed profile use its existing generator's `--check` command. Discover
the current generator list via `tools.release_gate.specification_checks()`;
do not guess flags. `tools.conformance` without `--write-report` executes cases
and checks the committed reports. After a reviewed implementation/fixture
change, `python -m tools.conformance --write-report` regenerates them. It does
not establish that new expected results are independently correct; inspect the
observation transitions against the baseline.

## Full native qualification

```text
python -m tools.release_gate --timings .runtime/task-proof/native
```

This runs architecture, specification/generator/conformance checks and native
test modules on the current OS. Its section is `all`, not one of the independent
CI specification/test-shard receipts accepted by `ci_artifacts accept`.

To follow the CI split on one OS:

```text
python -m tools.release_gate --section specification --timings .runtime/task-proof/specification
python -m tools.release_gate --section tests --qualification .runtime/task-proof/specification/gate.json --shard-count 6 --shard-index 0 --timings .runtime/task-proof/shard-0
```

The last command shows one shard. Use every index required by the current
workflow, with its matching specification parent and a separate output directory.
Do not claim complete CI acceptance from this single shard or a single OS.

## Shared wheel and installed execution

```text
python -m tools.ci_artifacts build --directory .runtime/task-proof/wheel
python -m tools.ci_artifacts verify-wheel --directory .runtime/task-proof/wheel
python -m tools.install_check --wheel-dir .runtime/task-proof/wheel --suite core --receipt .runtime/task-proof/installed-core.json
```

Build once for the qualification and use the same wheel/manifest thereafter.
`core` alone is a smoke check. Discover the full suite set in
`tools.installed_suites.SUITES` and the matrix in
`.github/workflows/r6-specification.yml`. Each required OS/suite has its own
receipt. `--suite all` is useful for local execution, but does not substitute
for the independent per-suite receipts expected by the CI audit.

## Recovery

```text
python -m tools.recovery_gate --proof managed --directory .runtime/task-proof/recovery/managed --wheel-dir .runtime/task-proof/wheel
```

`managed` is one example. Discover all required proof names and sizes in
`tools.recovery_gate.PROOFS` and the current workflow. Keep every proof in its
own directory. Do not lower workload, thresholds or budgets to obtain a pass.

## Audit existing CI artifacts

Select the workflow run by the **exact qualified source commit**, then check its
current status, conclusion, attempts and latest jobs. Download and verify the
required artifacts from that run. A workflow badge by itself is insufficient.

Collect the receipt artifacts in a fresh directory, preserving each artifact's
own subdirectory, including specification conformance reports and each recovery
`report.json`. Include the single wheel manifest. Keep unrelated JSON, old
attempts and the final summary outside this tree: the audit recursively reads
its JSON files and duplicate/stale receipts must fail.

```text
python -m tools.ci_artifacts accept --require-recovery --receipts .runtime/task-proof/receipts --output .runtime/task-proof/acceptance.json
```

The audit reuses current source identity, actual discovered test IDs and the
required wheel/suite/recovery inventories. It validates report checksums,
parent-child qualification and complete native/installed coverage. Read its
exit code and saved result. It does not fetch GitHub state, independently prove
an artifact's origin or compare old/new semantic observations: those checks are
separate parts of [the evidence workflow](evidence.md).

## Publication

Inspect `git diff --check`, staged/unstaged changes and generated reports.
When publication is authorized, push only the intended branch and changes,
then compare its remote head with the intended local commit using
`git ls-remote origin refs/heads/BRANCH` with the actual branch name.

A later docs-only commit may link a previously qualified application source
when its fingerprint is unchanged. Validate agent skills separately, and state
which commit had CI. Avoid a redundant full run for documentation alone.
