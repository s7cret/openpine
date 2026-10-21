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


def local_services(directory, *, coordinator_policy=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    lease = Lease(directory / ".manager.lock")
    db = None
    try:
        db = Database(directory / "catalog.sqlite3")
        objects = ObjectStore(directory / "objects", quota_bytes=quota(db))
        repo, limiter = JobRepository(db), WorkLimiter(coordinator_policy)
        market = CandleStore(directory, db, objects=objects)
        lifecycle = Lifecycle(db,directory,objects)
        preparation = Preparation(objects,market,repo,lifecycle.pin,limiter)
        queries = ResultReader(repo,objects,market,series_rows,limiter)
        return LocalServices(db,lease,repo,objects,market,preparation,queries,limiter,lifecycle)
    except BaseException:
        if db is not None:
            db.close()
        lease.close()
        raise


def local_manager(directory, *, coordinator_policy=None, **kwargs):
    services=local_services(directory,coordinator_policy=coordinator_policy)
    try:
        return JobManager(directory, services=services, **kwargs)
    except BaseException:
        services.close()
        raise
