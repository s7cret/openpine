#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV=0
WITH_UI=1

for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --no-ui) WITH_UI=0 ;;
    -h|--help)
      echo "Usage: ./scripts/install.sh [--dev] [--no-ui]"
      echo
      echo "Installs OpenPine and prints every install step."
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

cd "$ROOT"
echo "== OpenPine installer =="
echo "Root: $ROOT"
python --version
python -m pip --version

echo
echo "== Python package metadata =="
python - <<'PY'
import tomllib
from pathlib import Path

project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
print(f"name: {project['name']}")
print(f"version: {project['version']}")
print("dependencies:")
for dep in project.get("dependencies", []):
    print(f"  - {dep}")
if project.get("optional-dependencies", {}).get("dev"):
    print("dev dependencies:")
    for dep in project["optional-dependencies"]["dev"]:
        print(f"  - {dep}")
PY

echo
echo "== Installing Python package =="
python -m pip install --upgrade pip
if [[ "$DEV" == "1" ]]; then
  python -m pip install -e ".[dev]"
else
  python -m pip install -e .
fi

if [[ "$WITH_UI" == "1" ]]; then
  echo
  echo "== Installing UI dependencies =="
  if command -v npm >/dev/null 2>&1; then
    (cd openpine-ui && npm ci)
  else
    echo "npm not found; skipping UI install" >&2
  fi
fi

echo
echo "== Installed Python packages =="
python -m pip list
