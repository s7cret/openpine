"""Computation compatibility and full build provenance are separate identities."""

import hashlib
import sys
from importlib.metadata import version
from pathlib import Path

from packages.contracts.wire import digest, read_json

ROOT = Path(__file__).resolve().parents[2]
EXECUTION_SCHEME = "openpine.execution-identity.2"


def implementation_hash(root=ROOT):
    root=Path(root).resolve()
    files = [p for name in ("compiler", "contracts", "runtime", "simulation")
             for p in (root / "packages" / name).rglob("*.py")]
    files += [root / "packages/execution/session/__init__.py",root / "packages/execution/identity.py",
              root / "packages/execution/driver.py",root / "packages/execution/contexts.py",root / "packages/execution/trading.py",
              root / "packages/execution/realtime.py",root / "packages/execution/environment.py",root / 'packages/execution/observed_trading.py',root / 'packages/execution/intrabar.py',root / 'packages/execution/strategy_cycle.py',root / 'packages/execution/magnifier.py',root / 'packages/execution/magnifier_feed.py']
    files += [root / 'packages/execution/lower_contexts.py',root / 'spec/lower-tf-profile.json',root / 'packages/execution/lower_events.py',root / 'spec/lower-events-profile.json']
    files += [root / 'spec/prepared-requests-profile.json',root / 'spec/computed-targets-profile.json',root / 'spec/primary-targets-profile.json',root / 'spec/primary-targets-evidence.json']
    files += [root / 'spec/function-targets-profile.json',root / 'spec/scalar-targets-profile.json',root / 'spec/scalar-targets-evidence.json']
    files += [root / 'spec/numeric-targets-profile.json',root / 'spec/numeric-functions-evidence.json',root / 'spec/numeric-legacy-abi.json']
    files += [root / 'spec/dynamic-windows-profile.json',root / 'spec/dynamic-windows-evidence.json',root / 'spec/dynamic-windows-legacy-abi.json']
    files += [root / 'spec/dynamic-signals-profile.json',root / 'spec/dynamic-signals-evidence.json',root / 'spec/dynamic-signals-legacy-abi.json']
    files += [root / 'spec/history-storage-profile.json',root / 'spec/request-environment-profile.json']
    files += [root / 'spec/semantic-core-profile.json']
    files += [root / 'packages/execution/observation.py']
    files += [root / 'packages/storage/replay_tape.py']
    files += [root / 'spec/replay-profile.json',root / 'spec/observed-broker-profile.json',root / 'spec/intrabar-profile.json',root / 'spec/strategy-cycle-profile.json',root / 'spec/bar-magnifier-profile.json']
    # Indexed reads participate directly in Pine results and resume semantics.
    files += [root / 'packages/storage/trade_index.py',root / 'packages/storage/trades.py',
              root / 'packages/storage/checkpoints.py',root / 'packages/storage/results.py',
              root / 'packages/storage/checkpoint_records.py',root / 'packages/storage/checkpoint_tree.py']
    return digest({"scheme": EXECUTION_SCHEME, "files": {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)}, "python": list(sys.version_info[:3]), "jsonschema": version("jsonschema")})


def build_identity(root=ROOT):
    root=Path(root).resolve()
    files = [p for name in ("packages", "apps") for p in (root / name).rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    files += [root / "pyproject.toml", root / "spec/lock.json"]
    return {"scheme": "openpine.build-identity.1", "sha256": digest({
        "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(files)}, "python": list(sys.version_info[:3]),
        "dependencies": {name: version(name) for name in ("jsonschema", "apsw", "duckdb")}})}


def plan_identity():
    return implementation_hash(), digest(read_json(ROOT / "spec/lock.json"))
