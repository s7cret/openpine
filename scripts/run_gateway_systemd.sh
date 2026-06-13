#!/usr/bin/env bash
set -euo pipefail

ROOT="${OPENPINE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi

: "${OPENPINE_HOST:=0.0.0.0}"
: "${OPENPINE_PORT:=8080}"
: "${OPENPINE_LOG_LEVEL:=info}"

exec "${OPENPINE_PYTHON:-python}" -c "import os, sys; sys.path.insert(0, '.'); from openpine.gateway.server import create_app; import uvicorn; uvicorn.run(create_app(), host=os.environ.get('OPENPINE_HOST', '0.0.0.0'), port=int(os.environ.get('OPENPINE_PORT', '8080')), log_level=os.environ.get('OPENPINE_LOG_LEVEL', 'info'))"
