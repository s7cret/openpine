"""Assert actual cancelled command and stable process-family cleanup receipts."""
from pathlib import Path
from openpine.verification.execution_identity import evidence_path, hash_file
from openpine.verification.identity import read_json

def assert_cancelled_family(output, attempt):
    descriptor = attempt['artifacts']['process-family']
    path = evidence_path(Path(output), descriptor['path'])
    assert hash_file(path) == descriptor['sha256']
    family = read_json(path)
    assert attempt['returncode'] == 70
    assert family['reason'] == 'cancelled'
    assert family['command_returncode'] < 0
    assert family['cleanup_verified'] is True
    assert family['surviving_processes'] == []
    assert family['observation_errors'] == []
