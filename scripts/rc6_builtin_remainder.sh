#!/usr/bin/env bash
# Existing independent oracle/remainder owner; not a stabilization PASS shortcut.
set -euo pipefail
MODE=strict
FLAGS=()
case "${1:-}" in
  '') ;;
  --diagnostic-provisional) MODE=diagnostic-provisional; FLAGS=(--diagnostic-provisional); shift ;;
  *) printf 'unknown language reporting mode: %s\n' "$1" >&2; exit 2 ;;
esac
test "$#" -eq 0
python scripts/derive_numeric_closure.py --check
EXPECTED_PLAN=$(python -c 'import json; print(json.load(open("verification/stage2-evidence-plan-lock.json"))["plan_hash"])')
INDEX_STATUS=0
python -m openpine.verification builtin-index --host-root "$GITHUB_WORKSPACE" --evidence "$RUNNER_TEMP/evidence" --surface-lock verification/stage2-callable-lock.json --plan verification/stage2-evidence-plan.json --expected-plan-hash "$EXPECTED_PLAN" --output "$RUNNER_TEMP/evidence/builtin-index.json" "${FLAGS[@]}" || INDEX_STATUS=$?
REMAINDER_STATUS=0
python -m openpine.verification stage2-remaining --host-root "$GITHUB_WORKSPACE" --builtin-index "$RUNNER_TEMP/evidence/builtin-index.json" --output "$RUNNER_TEMP/evidence/stage2-remaining.json" "${FLAGS[@]}" || REMAINDER_STATUS=$?
# Raw exits remain distinct from semantic acceptance. No arbitrary nonzero exit
# is admitted as expected debt: only the existing explicit diagnostic owner can
# return report-generation success for independently admitted provisional debt.
python - "$RUNNER_TEMP/evidence/language-command-exits.json" "$MODE" "$INDEX_STATUS" "$REMAINDER_STATUS" <<'PYEXITS'
import json, pathlib, sys
path, mode, index, remainder = sys.argv[1:]
with pathlib.Path(path).open('x') as stream:
    json.dump({'scope': 'language report generation; not stabilization',
               'mode': mode, 'builtin_index_exit': int(index),
               'remainder_exit': int(remainder),
               'report_generation_completed': int(index) == int(remainder) == 0,
               'language_accepted': False}, stream, indent=2)
PYEXITS
test "$INDEX_STATUS" -eq 0
test "$REMAINDER_STATUS" -eq 0
