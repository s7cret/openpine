"""Bounded inline diagnostics with one immutable, strictly read JSONL overflow."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from openpine.verification.execution_identity import evidence_path, hash_file
from openpine.verification.identity import canonical

RECORD_LIMIT = 4096
INLINE_LIMIT = 64


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate disk record key')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('nonfinite disk record value')


def validate_record(row):
    if (not isinstance(row, dict) or set(row) != {'observed_at', 'free_bytes', 'error'}
            or not isinstance(row['observed_at'], str) or not row['observed_at']
            or (row['free_bytes'] is not None and (type(row['free_bytes']) is not int or row['free_bytes'] < 0))
            or (row['error'] is not None and (not isinstance(row['error'], str) or not row['error']))
            or (row['free_bytes'] is None and row['error'] is None)):
        raise ValueError('invalid disk observation evidence')
    return row


class ObservationWriter:
    """All details, at most limit inline records, and a single constant descriptor.

    Failed writes retain the new .partial file; it is never admitted as evidence.
    Every append flushes so write errors reach the active scheduler immediately.
    """
    def __init__(self, root: Path, limit: int = INLINE_LIMIT):
        self.root, self.limit = root, limit
        self.observations = []
        self.stream = None
        self.partial = evidence_path(root, 'disk-observations.partial', must_exist=False)
        self.final = evidence_path(root, 'disk-observations.jsonl', must_exist=False)

    def _write(self, row):
        payload = canonical(validate_record(row)) + b'\n'
        if len(payload) > RECORD_LIMIT:
            raise ValueError('oversized disk record')
        self.stream.write(payload)
        self.stream.flush()

    def append(self, row):
        validate_record(row)
        if len(canonical(row)) + 1 > RECORD_LIMIT:
            raise ValueError('oversized disk record')
        if self.stream is None and len(self.observations) < self.limit:
            self.observations.append(row)
            return
        if self.stream is None:
            self.stream = self.partial.open('xb')
            for previous in self.observations:
                self._write(previous)
            self.observations.clear()
        self._write(row)

    def finish(self):
        if self.stream is None:
            return None
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        # Hard-link publication is atomic and refuses replacement, including symlinks.
        os.link(self.partial, self.final)
        self.partial.unlink()
        return {'path': self.final.relative_to(self.root).as_posix(), 'sha256': hash_file(self.final)}

    def close_partial(self):
        if self.stream is not None and not self.stream.closed:
            self.stream.close()


def iter_observations(root: Path, disk: dict):
    """Authenticate all stream bytes, parse bounded records, then check read hash.

    Consumers must exhaust this iterator before accepting counters/provenance.
    """
    rows = disk.get('observations')
    if not isinstance(rows, list):
        raise ValueError('disk guard has no observation evidence')
    desc = disk.get('observation_stream')
    if desc is None:
        for row in rows:
            yield validate_record(row)
        return
    if rows or not isinstance(desc, dict) or set(desc) != {'path', 'sha256'}:
        raise ValueError('invalid disk stream descriptor or mixed inline evidence')
    path = evidence_path(root, desc['path'])
    if hash_file(path) != desc['sha256']:
        raise ValueError('disk stream checksum mismatch')
    digest = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as stream:
        while True:
            line = stream.readline(RECORD_LIMIT + 1)
            if not line:
                break
            if len(line) > RECORD_LIMIT or not line.endswith(b'\n'):
                raise ValueError('oversized or truncated disk record')
            digest.update(line)
            try:
                row = json.loads(line, object_pairs_hook=_pairs, parse_constant=_nonfinite)
            except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
                raise ValueError('malformed disk record') from error
            yield validate_record(row)
    after = path.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or 'sha256:' + digest.hexdigest() != desc['sha256']):
        raise ValueError('disk stream changed during read')
