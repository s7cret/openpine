"""Consistent SQLite backups plus immutable objects; restore only to a new root."""

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from packages.contracts.wire import immutable_json, read_json
from .database import Database
from .objects import ObjectStore, file_hash, sync_directory
from .lifecycle import reachable


def audit(database, root):
    """Check both database integrity and every referenced immutable object."""
    database.check()
    reachable(database, ObjectStore(Path(root) / "objects"))


def durable_copy(source, target):
    from .disk import guard
    guard(target.parent,Path(source).stat().st_size)
    shutil.copyfile(source, target)
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())


def publish_directory(source, target):
    """Windows scanners can briefly retain handles after all writers close."""
    deadline=time.monotonic()+2
    while True:
        if target.exists(): raise FileExistsError('Publication destination must remain new.')
        try:
            source.rename(target)
            return
        except PermissionError as error:
            if os.name!='nt' or getattr(error,'winerror',None) not in (5,32,33) or time.monotonic()>=deadline:
                raise
            time.sleep(.02)


def backup(database, root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination == root or root in destination.parents:
        raise ValueError("Backup destination must be new and outside the live store.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    from .disk import DiskAdmission
    size=sum(p.stat().st_size for p in (root/'objects').glob('[0-9a-f][0-9a-f]/*') if p.is_file())+sum(p.stat().st_size for p in root.glob('catalog.sqlite3*'))
    with DiskAdmission(destination.parent).slot(size,kind='backup'),tempfile.TemporaryDirectory(prefix=".backup-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        (temporary / "objects").mkdir()
        database.backup(temporary / "catalog.sqlite3")
        objects = ObjectStore(root / "objects")
        copied = Database(temporary / "catalog.sqlite3")
        try:
            marked = reachable(copied,objects)
        finally:
            copied.close()
        files = {}
        for sha in sorted(marked):
            source = objects.verify(sha)
            relative = source.relative_to(root)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            durable_copy(source, target)
            if file_hash(target) != source.name:
                raise ValueError("Backup object copy failed verification.")
            files[relative.as_posix()] = source.name
        files["catalog.sqlite3"] = file_hash(temporary / "catalog.sqlite3")
        copied = Database(temporary / "catalog.sqlite3")
        try:
            audit(copied, temporary)
        finally:
            copied.close()
        # Opening the copy may checkpoint it; checksum its final closed bytes.
        files["catalog.sqlite3"] = file_hash(temporary / "catalog.sqlite3")
        immutable_json(temporary / "backup.json", {"schema_version": "openpine.backup.1", "files": files})
        for directory in (temporary / "objects").iterdir():
            if directory.is_dir():
                sync_directory(directory)
        sync_directory(temporary / "objects"); sync_directory(temporary)
        publish_directory(temporary,destination)
        sync_directory(destination.parent)
    return destination


def restore(source, destination, *, migrate=True):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError("Restore destination must be new and outside the backup.")
    manifest = read_json(source / "backup.json")
    if manifest.keys() != {"schema_version", "files"} or manifest["schema_version"] != "openpine.backup.1":
        raise ValueError("Unknown backup schema.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    from .disk import DiskAdmission
    size=sum((source/name).stat().st_size for name in manifest['files'] if name=='catalog.sqlite3' or re.fullmatch(r'objects/[0-9a-f]{2}/[0-9a-f]{64}',name))
    with DiskAdmission(destination.parent).slot(size,kind='restore'),tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        for name, sha in manifest["files"].items():
            path = Path(name)
            if name != "catalog.sqlite3" and not re.fullmatch(r"objects/[0-9a-f]{2}/[0-9a-f]{64}", name):
                raise ValueError("Invalid backup member path.")
            original, target = source / path, temporary / path
            if not original.resolve().is_relative_to(source) or original.is_symlink() or file_hash(original) != sha:
                raise ValueError("Backup hash mismatch.")
            target.parent.mkdir(parents=True, exist_ok=True)
            durable_copy(original, target)
            if file_hash(target) != sha:
                raise ValueError("Restore copy hash mismatch.")
        if migrate:
            db = Database(temporary / "catalog.sqlite3")
            try:
                audit(db, temporary)
            finally:
                db.close()
        else:
            from .inspection import ReadDatabase
            with ReadDatabase(temporary/'catalog.sqlite3') as db: db.check()
        for directory in sorted(temporary.rglob("*"),reverse=True):
            if directory.is_dir():
                sync_directory(directory)
        sync_directory(temporary)
        publish_directory(temporary,destination)
        sync_directory(destination.parent)
    return destination
