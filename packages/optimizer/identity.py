"""Search algorithm/evaluator compatibility is separate from Pine execution identity."""
import hashlib
from pathlib import Path
import sys
from packages.contracts.wire import digest


def identity():
    root=Path(__file__).resolve().parents[2]
    paths=[*sorted((root/'packages/optimizer').glob('*.py')),root/'packages/contracts/optimization.py']
    return {'scheme':'openpine.optimizer-identity.1','sha256':digest({
        'python':list(sys.version_info[:3]),'files':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}})}
