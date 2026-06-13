#!/usr/bin/env bash
set -euo pipefail
${PYTHON:-python} - <<'PY'
import tempfile
from pathlib import Path

import openpine
from openpine.gateway.server import create_app
from openpine.storage.sqlite_storage import SQLiteStorage

print(openpine.__version__)
app = create_app()
assert app.title == "OpenPine Gateway"
with tempfile.TemporaryDirectory() as tmp:
    with SQLiteStorage(Path(tmp) / "openpine.sqlite") as storage:
        assert storage.path is not None
PY
