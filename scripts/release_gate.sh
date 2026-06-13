#!/usr/bin/env bash
set -euo pipefail
PYTHON=${PYTHON:-python}
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"
export DD_TRACE_ENABLED=${DD_TRACE_ENABLED:-false}

$PYTHON -m compileall -q openpine tests
$PYTHON -m ruff check openpine scripts --select F,E9
$PYTHON -m pytest -q -p no:ddtrace --cov=openpine --cov-report=term
find . -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf .coverage .pytest_cache .ruff_cache .mypy_cache .openpine
$PYTHON -m openpine.quality duplicates openpine
$PYTHON -m openpine.quality architecture openpine --max-lines 4000
$PYTHON -m openpine.distribution manifest --root .
$PYTHON -m openpine.release --root .
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
