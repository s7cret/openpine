"""Content-addressed files. Durable publication precedes catalog references."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from packages.contracts.wire import canonical, loads


def identifier(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid content identifier.")
    return value


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_directory(directory):
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class ObjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha):
        identifier(sha)
        return self.root / sha[:2] / sha

    def verify(self, sha):
        path = self.path(sha)
        if file_hash(path) != sha:
            raise ValueError("Immutable object hash mismatch.")
        return path

    def publish(self, temporary):
        temporary = Path(temporary)
        sha = file_hash(temporary)
        path = self.path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
            sync_directory(path.parent)
        except FileExistsError:
            self.verify(sha)
        return sha

    def put(self, value):
        raw = canonical(value)
        fd, name = tempfile.mkstemp(prefix=".object-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            return self.publish(name)
        finally:
            Path(name).unlink(missing_ok=True)

    def get(self, sha, max_bytes=16 * 1024 * 1024):
        path = self.verify(sha)
        with path.open("rb") as stream:
            return loads(stream.read(max_bytes + 1), max_bytes=max_bytes)
