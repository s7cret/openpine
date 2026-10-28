"""Exclusive, receipted upgrade with an exact pre-migration backup."""
import re
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from packages.contracts.wire import atomic_json, immutable_json, read_json
from packages.contracts import OpenPineError
from .backup import durable_copy, publish_directory
from .database import Database, SCHEMA_VERSION
from .inspection import ReadDatabase, ReadObjects
from .lease import Lease
from .objects import file_hash, sync_directory


def verify_backup(root):
    root = Path(root).resolve()
    manifest = read_json(root/'backup.json')
    if manifest.keys() != {'schema_version','files'} or manifest['schema_version'] != 'openpine.backup.1':
        raise ValueError('Invalid upgrade backup.')
    if 'catalog.sqlite3' not in manifest['files']: raise ValueError('Backup has no catalog.')
    for name, sha in manifest['files'].items():
        if name != 'catalog.sqlite3' and not re.fullmatch(r'objects/[0-9a-f]{2}/[0-9a-f]{64}', name):
            raise ValueError('Invalid backup path.')
        path = root/name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or file_hash(path) != sha:
            raise ValueError('Upgrade backup hash mismatch.')
    with ReadDatabase(root/'catalog.sqlite3') as db: db.check()
    return manifest


def _bound_backup(receipt, destination):
    verify_backup(destination)
    if read_json(destination/'upgrade-origin.json')!={'upgrade_id':receipt['upgrade_id'],'schema':receipt['from_schema']}:
        raise ValueError('Upgrade backup belongs to another operation.')
    if receipt.get('backup_sha256') and file_hash(destination/'backup.json')!=receipt['backup_sha256']:
        raise ValueError('Upgrade backup manifest changed.')


def exact_backup(db, root, destination, *, upgrade_id):
    """Keep all immutable objects, including roots unknown to this schema version."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination == root or root in destination.parents or destination in root.parents:
        raise ValueError('Upgrade backup must be new and outside the store.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    objects = ReadObjects(root/'objects')
    members = sorted(objects.root.glob('[0-9a-f][0-9a-f]/*'))
    members = [p for p in members if re.fullmatch('[0-9a-f]{64}',p.name)]
    size = sum(p.stat().st_size for p in members) + (root/'catalog.sqlite3').stat().st_size*2
    from .disk import DiskAdmission
    with DiskAdmission(destination.parent).slot(size,kind='upgrade-backup'), tempfile.TemporaryDirectory(prefix='.upgrade-backup-',dir=destination.parent) as temporary:
        target = Path(temporary)
        (target/'objects').mkdir()
        db.backup(target/'catalog.sqlite3')
        files = {'catalog.sqlite3': file_hash(target/'catalog.sqlite3')}
        for path in members:
            if path.parent.name != path.name[:2]: raise ValueError('Invalid immutable object location.')
            original = objects.verify(path.name)
            name = original.relative_to(root).as_posix()
            output = target/name; output.parent.mkdir(exist_ok=True)
            durable_copy(original,output)
            if file_hash(output) != path.name: raise ValueError('Backup copy differs.')
            files[name] = path.name
        immutable_json(target/'backup.json',{'schema_version':'openpine.backup.1','files':files})
        immutable_json(target/'upgrade-origin.json',{'upgrade_id':upgrade_id,'schema':db.version})
        verify_backup(target)
        for directory in (target/'objects').iterdir(): sync_directory(directory)
        sync_directory(target/'objects'); sync_directory(target)
        publish_directory(target,destination); sync_directory(destination.parent)
    return destination


def upgrade(root, destination, *, target_identity, hook=lambda phase: None, migration_hook=None):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if not (root/'catalog.sqlite3').is_file(): raise ValueError('CATALOG_MISSING')
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError('Upgrade backup must be outside the store.')
    with Lease(root/'.manager.lock'), ExitStack() as workers:
        attempts=[p for p in (root/'attempts').glob('*/*') if p.is_dir()]
        attempts += [p for p in root.glob('replay-attempt-*') if p.is_dir()]
        for attempt in attempts:
            if attempt.is_symlink() or not attempt.resolve().is_relative_to(root):raise ValueError('Unsafe attempt lease path.')
            try:workers.enter_context(Lease(attempt/'.active.lock'))
            except OpenPineError as error:
                raise ValueError('WORKER_ACTIVE: wait for the previous worker to exit.') from error
        receipt_path = root/'.upgrade.json'
        receipt = read_json(receipt_path) if receipt_path.exists() else None
        if receipt and receipt.get('phase')=='complete' and receipt.get('backup')==str(destination) and receipt.get('target_identity')==target_identity:
            _bound_backup(receipt,destination)
            with ReadDatabase(root/'catalog.sqlite3') as db:
                if db.version!=receipt['to_schema']:raise ValueError('Completed upgrade no longer matches the catalog version.')
            return receipt  # Repeated command after a lost successful response.
        if receipt and receipt.get('phase') != 'complete':
            if receipt.get('schema_version') != 'openpine.upgrade.1' or receipt.get('backup') != str(destination) or receipt.get('target_identity') != target_identity:
                raise ValueError('UPGRADE_TARGET_CHANGED: use the original target build and backup.')
        else:
            if destination.exists(): raise ValueError('Upgrade backup destination must be new.')
            with ReadDatabase(root/'catalog.sqlite3') as db:
                if db.version > SCHEMA_VERSION: raise ValueError('CATALOG_NEWER')
                receipt = {'schema_version':'openpine.upgrade.1','phase':'planned','from_schema':db.version,
                           'to_schema':SCHEMA_VERSION,'backup':str(destination),'target_identity':target_identity,
                           'started_at':time.time(),'upgrade_id':uuid.uuid4().hex}
            atomic_json(receipt_path,receipt)
        hook('planned')
        if destination.exists():
            _bound_backup(receipt,destination)
        else:
            if receipt['phase'] != 'planned': raise ValueError('UPGRADE_BACKUP_MISSING')
            with ReadDatabase(root/'catalog.sqlite3') as db:
                db.check(); exact_backup(db,root,destination,upgrade_id=receipt['upgrade_id'])
        # A published backup survives a crash before this receipt update.
        receipt.update(phase='backed_up',backup_sha256=file_hash(destination/'backup.json')); atomic_json(receipt_path,receipt); hook('backed_up')
        db = Database(root/'catalog.sqlite3',maintenance=True,migration_hook=migration_hook)
        try:
            receipt.update(phase='migrated'); atomic_json(receipt_path,receipt); hook('migrated')
            from .backup import audit
            audit(db,root)
            hook('verified')
        finally: db.close()
        receipt.update(phase='complete',finished_at=time.time()); atomic_json(receipt_path,receipt)
        return receipt
