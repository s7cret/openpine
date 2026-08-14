#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${OPENPINE_UI_STATIC_ROOT:=$ROOT/dist}"
: "${OPENPINE_API_TARGET:=http://127.0.0.1:8080}"
: "${OPENPINE_UI_HOST:=0.0.0.0}"
: "${OPENPINE_UI_PORT:=1888}"

export OPENPINE_UI_STATIC_ROOT OPENPINE_API_TARGET OPENPINE_UI_HOST OPENPINE_UI_PORT
exec /usr/bin/node "$ROOT/tools/serve-production.mjs"
