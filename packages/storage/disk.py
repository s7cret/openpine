"""Cross-process scratch reservations, reclaimed by OS lease after a crash."""
import shutil,uuid
from contextlib import contextmanager
from pathlib import Path
from packages.contracts import OpenPineError
from packages.contracts.disk import DiskBusy
from packages.contracts.wire import atomic_json,read_json
from .lease import Lease

MIB=1024**2
class DiskAdmission:
    def __init__(self,root,*,reserve_bytes=64*MIB,probe=shutil.disk_usage):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.directory=self.root/'.disk-reservations';self.directory.mkdir(exist_ok=True)
        self.reserve_bytes,self.probe=reserve_bytes,probe;self.held={}

    def _pending(self):
        total=0
        for path in self.directory.glob('*.json'):
            token=path.stem;lock=path.with_suffix('.lease')
            if token not in self.held:
                try:lease=Lease(lock)
                except OpenPineError:pass
                else:
                    lease.close();path.unlink(missing_ok=True);lock.unlink(missing_ok=True);continue
            value=read_json(path,max_bytes=4096)
            if type(value.get('bytes')) is not int or value['bytes']<0:raise ValueError('Invalid disk reservation.')
            total+=value['bytes']
        for lock in self.directory.glob("*.lease"):
            if lock.with_suffix(".json").exists():continue
            try:lease=Lease(lock)
            except OpenPineError:continue
            lease.close();lock.unlink(missing_ok=True)
        return total

    def snapshot(self):
        with Lease(self.directory/'catalog.lock',wait_seconds=10):
            pending=self._pending();usage=self.probe(self.root)
            return dict(schema_version='openpine.disk-admission.1',available_bytes=usage.free,
                pending_reservation_bytes=pending,os_reserve_bytes=self.reserve_bytes)

    def reserve(self,amount,*,kind='scratch'):
        if type(amount) is not int or amount<0:raise ValueError('Invalid disk reservation size.')
        token=uuid.uuid4().hex
        with Lease(self.directory/'catalog.lock',wait_seconds=10):
            self._headroom(amount,kind)
            lease=Lease(self.directory/(token+'.lease'))
            self.held[token]=lease
            try:atomic_json(self.directory/(token+'.json'),{'bytes':amount,'kind':kind})
            except BaseException:self.held.pop(token).close();raise
        return token

    def _headroom(self,amount,kind):
        pending=self._pending();available=self.probe(self.root).free
        if available<self.reserve_bytes+pending+amount:
            raise DiskBusy(dict(schema_version='openpine.admission.1',reason='disk',kind=kind,retryable=True,
                retry_after_ms=1000,available_bytes=available,required_available_bytes=self.reserve_bytes+pending+amount,
                requested_bytes=amount,pending_reservation_bytes=pending,os_reserve_bytes=self.reserve_bytes))

    def check(self,amount,*,kind='scratch'):
        if type(amount) is not int or amount<0:raise ValueError('Invalid disk check size.')
        with Lease(self.directory/'catalog.lock',wait_seconds=10):self._headroom(amount,kind)

    def release(self,token):
        with Lease(self.directory/'catalog.lock',wait_seconds=10):
            if token in self.held:
                (self.directory/(token+'.json')).unlink(missing_ok=True)
                self.held.pop(token).close();(self.directory/(token+'.lease')).unlink(missing_ok=True)

    def materialized(self,token,remaining):
        with Lease(self.directory/'catalog.lock',wait_seconds=10):
            if token not in self.held:raise ValueError('Disk reservation belongs to another owner.')
            path=self.directory/(token+'.json');value=read_json(path,max_bytes=4096)
            if type(remaining) is not int or not 0<=remaining<=value['bytes']:raise ValueError('Disk reservation may only shrink.')
            atomic_json(path,{**value,'bytes':remaining})

    @contextmanager
    def slot(self,amount,*,kind='scratch'):
        token=self.reserve(amount,kind=kind)
        try:yield
        finally:self.release(token)

def scratch_root(path):
    """Use the owning store when present; standalone export uses its own volume."""
    path=Path(path).resolve()
    for parent in (path,*path.parents):
        if (parent/'catalog.sqlite3').exists():return parent
    return path if path.is_dir() else path.parent

def guard(path,amount):
    """Short writes also preserve the free-space floor, without a persistent lease."""
    disk=DiskAdmission(scratch_root(path))
    disk.check(amount)
