"""Composition root: concrete local adapters are assembled only in the app."""

from dataclasses import dataclass
from pathlib import Path

from packages.execution.jobs import JobManager
from packages.execution.limits import WorkLimiter
from packages.execution.preparation import Preparation
from packages.execution.queries import ResultReader
from packages.storage.candles import CandleStore
from packages.storage.database import Database
from packages.storage.jobs import JobRepository
from packages.storage.lease import Lease
from packages.storage.lifecycle import Lifecycle, quota
from packages.storage.objects import ObjectStore
from packages.storage.results import series_rows
from packages.storage.trades import event_rows
from packages.storage.checkpoints import CheckpointStore


@dataclass
class LocalServices:
    db: object
    lease: object
    repository: object
    objects: object
    market: object
    preparation: object
    queries: object
    limiter: object
    lifecycle: object
    checkpoints: object
    scratch: object
    traces: object
    closed: bool = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.db.close()
        finally:
            self.lease.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()


def local_services(directory, *, coordinator_policy=None,host_admission=None,disk_admission=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    lease = Lease(directory / ".manager.lock")
    db = None
    try:
        db = Database(directory / "catalog.sqlite3",require_current=True)
        objects = ObjectStore(directory / "objects", quota_bytes=quota(db))
        repo, limiter = JobRepository(db), WorkLimiter(coordinator_policy,host=host_admission)
        from packages.storage.disk import DiskAdmission
        limiter.disk=disk_admission or DiskAdmission(directory)
        market = CandleStore(directory, db, objects=objects)
        lifecycle = Lifecycle(db,directory,objects)
        preparation = Preparation(objects,market,repo,lifecycle.pin,limiter)
        queries = ResultReader(repo,objects,market,series_rows,limiter,event_rows)
        from packages.storage.scratch import AttemptScratch
        from packages.storage.traces import TraceStore
        return LocalServices(db,lease,repo,objects,market,preparation,queries,limiter,lifecycle,CheckpointStore(objects),AttemptScratch(directory/"attempts"),TraceStore(db,objects,lifecycle))
    except BaseException:
        if db is not None:
            db.close()
        lease.close()
        raise


def local_manager(directory, *, coordinator_policy=None,host_admission=None,disk_admission=None, **kwargs):
    services=local_services(directory,coordinator_policy=coordinator_policy,host_admission=host_admission,disk_admission=disk_admission)
    try:
        return JobManager(directory, services=services, **kwargs)
    except BaseException:
        services.close()
        raise
