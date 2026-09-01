#!/usr/bin/env bash
set -euo pipefail
export PYTHON=${PYTHON:-python}
export DD_TRACE_ENABLED=${DD_TRACE_ENABLED:-false}

$PYTHON -m compileall -q openpine tests
$PYTHON -m ruff check openpine scripts
$PYTHON -m pytest -q -p no:ddtrace --cov=openpine --cov-report=term
find . -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf .coverage .pytest_cache .ruff_cache .mypy_cache .openpine
$PYTHON -m openpine.quality duplicates openpine
$PYTHON -m openpine.quality architecture openpine --max-lines 4000
$PYTHON -m openpine.distribution manifest --root .
candidate_manifest_path=${OPENPINE_CANDIDATE_MANIFEST:-}
if [[ -z "$candidate_manifest_path" && -f candidates/stack-candidate-5.0.0-rc.6.template.json ]]; then
    echo "materialized candidate manifest required: set OPENPINE_CANDIDATE_MANIFEST" >&2
    exit 1
fi
if [[ -n "$candidate_manifest_path" ]]; then
    test -f "$candidate_manifest_path"
    candidate_root=$(dirname "$candidate_manifest_path")
    candidate_name=$($PYTHON scripts/resolve_stack_candidate.py \
        --root "$candidate_root" --require-stage wheel-bound)
    test "$candidate_root/$candidate_name" = "$candidate_manifest_path"
    $PYTHON -m pytest -q tests/test_stack_candidate.py
else
    $PYTHON -m openpine.release --root .
fi
$PYTHON - <<'PY'
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.db_health import schema_health

with NamedTemporaryFile(suffix='.sqlite', delete=False) as handle:
    db_path = Path(handle.name)
try:
    with SQLiteStorage(db_path) as storage:
        MigrationRunner().run_migrations(storage)
        report = schema_health(storage)
        if not report.ok:
            raise SystemExit(f'storage health failed: {report}')
finally:
    db_path.unlink(missing_ok=True)
PY
bash scripts/smoke_import_parse.sh
