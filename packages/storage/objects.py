"""Content-addressed files. Durable publication precedes catalog references."""

import hashlib
import errno
import json
import shutil
import os
import re
import tempfile
from pathlib import Path

from packages.contracts.wire import canonical, loads
from .lease import Lease


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
    def __init__(self, root, *, quota_bytes=0):
        if type(quota_bytes) is not int or quota_bytes < 0:
            raise ValueError("Invalid immutable object quota.")
        self.root = Path(root)
        self.quota_bytes = quota_bytes
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
        if self.quota_bytes:
            with Lease(self.root / ".publication.lock", wait_seconds=10):
                if not path.exists():
                    used = sum(p.stat().st_size for p in self.root.glob("[0-9a-f][0-9a-f]/*") if p.is_file())
                    if used + temporary.stat().st_size > self.quota_bytes:
                        from packages.contracts.disk import StorageQuota
                        raise StorageQuota()
                return self._publish(temporary, path, sha)
        return self._publish(temporary, path, sha)

    def _publish(self, temporary, path, sha):
        path.parent.mkdir(parents=True, exist_ok=True)
        sync_directory(self.root)
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
        from .disk import guard
        guard(self.root,len(raw)*2)
        fd, name = tempfile.mkstemp(prefix=".object-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                # publish() owns the durable file barrier before linking CAS.
            return self.publish(name)
        finally:
            Path(name).unlink(missing_ok=True)

    def put_stream(self,value,*,max_bytes=256*1024*1024):
        from .disk import guard
        guard(self.root,min(max_bytes,1024**2))
        fd,name=tempfile.mkstemp(prefix=".state-",dir=self.root)
        try:
            size=0
            encoder=json.JSONEncoder(ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
            with os.fdopen(fd,"wb") as stream:
                for chunk in encoder.iterencode(value):
                    raw=chunk.encode("utf-8");size+=len(raw)
                    if size>max_bytes:raise ValueError("Checkpoint state exceeds 256 MiB.")
                    if size//(1024**2)!=(size-len(raw))//(1024**2):guard(self.root,1024**2+len(raw))
                    stream.write(raw)
                stream.flush()  # publish() synchronizes the completed file.
            return self.publish(name)
        finally:Path(name).unlink(missing_ok=True)

    def copy_file(self,source):
        from .disk import guard
        guard(self.root,Path(source).stat().st_size)
        fd,name=tempfile.mkstemp(prefix=".prefix-",dir=self.root);os.close(fd)
        try:
            shutil.copyfile(source,name)
            return self.publish(name)
        finally:Path(name).unlink(missing_ok=True)

    def get(self, sha, max_bytes=16 * 1024 * 1024):
        path = self.verify(sha)
        with path.open("rb") as stream:
            return loads(stream.read(max_bytes + 1), max_bytes=max_bytes)
