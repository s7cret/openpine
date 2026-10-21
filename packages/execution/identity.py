"""Computation compatibility and full build provenance are separate identities."""

import hashlib
import sys
from importlib.metadata import version
from pathlib import Path

from packages.contracts.wire import digest, read_json

ROOT = Path(__file__).resolve().parents[2]
EXECUTION_SCHEME = "openpine.execution-identity.2"


def implementation_hash(root=ROOT):
    files = [p for name in ("compiler", "contracts", "runtime")
             for p in (root / "packages" / name).rglob("*.py")]
    files += [root / "packages/execution/session/__init__.py", Path(__file__) if root == ROOT
              else root / "packages/execution/identity.py",root / "packages/execution/driver.py",root / "packages/execution/contexts.py"]
    return digest({"scheme": EXECUTION_SCHEME, "files": {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)}, "python": list(sys.version_info[:3]), "jsonschema": version("jsonschema")})


def build_identity(root=ROOT):
    files = [p for name in ("packages", "apps") for p in (root / name).rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    files += [root / "pyproject.toml", root / "spec/lock.json"]
    return {"scheme": "openpine.build-identity.1", "sha256": digest({
        "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(files)}, "python": list(sys.version_info[:3]),
        "dependencies": {name: version(name) for name in ("jsonschema", "apsw", "duckdb")}})}


def plan_identity():
    return implementation_hash(), digest(read_json(ROOT / "spec/lock.json"))
