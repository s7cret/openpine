#!/usr/bin/env bash
# Existing independent oracle/remainder owner; not a stabilization PASS shortcut.
set -euo pipefail
python scripts/derive_numeric_closure.py --check
EXPECTED_PLAN=$(python -c 'import json; print(json.load(open("verification/stage2-evidence-plan-lock.json"))["plan_hash"])')
INDEX_STATUS=0
python -m openpine.verification builtin-index --host-root "$GITHUB_WORKSPACE" --evidence "$RUNNER_TEMP/evidence" --surface-lock verification/stage2-callable-lock.json --plan verification/stage2-evidence-plan.json --expected-plan-hash "$EXPECTED_PLAN" --output "$RUNNER_TEMP/evidence/builtin-index.json" || INDEX_STATUS=$?
REMAINDER_STATUS=0
python -m openpine.verification stage2-remaining --host-root "$GITHUB_WORKSPACE" --builtin-index "$RUNNER_TEMP/evidence/builtin-index.json" --output "$RUNNER_TEMP/evidence/stage2-remaining.json" || REMAINDER_STATUS=$?
test "$INDEX_STATUS" -eq 0
test "$REMAINDER_STATUS" -eq 0
