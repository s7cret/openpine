"""Reap disposable historical scratch only while its attempt lease is unowned."""
import re,shutil
from pathlib import Path
from packages.contracts import OpenPineError
from .lease import Lease

class AttemptScratch:
    def __init__(self,root):self.root=Path(root).resolve()

    def clean(self,job_id):
        if type(job_id) is not str or not re.fullmatch('[0-9a-f]{32}',job_id):raise ValueError('Invalid scratch owner.')
        job=self.root/job_id
        if not job.exists():return False
        if job.is_symlink() or not job.resolve().is_relative_to(self.root):raise ValueError('Unsafe scratch job path.')
        pending=False
        for attempt in job.iterdir():
            if not re.fullmatch('[0-9a-f]{32}',attempt.name) or not attempt.is_dir():continue
            if attempt.is_symlink() or not attempt.resolve().is_relative_to(job):raise ValueError('Unsafe scratch attempt path.')
            try:lease=Lease(attempt/'.active.lock')
            except OpenPineError:pending=True;continue
            try:
                for path in attempt.rglob('*'):
                    if path.is_file() and path.suffix in ('.csv','.parquet','.tmp'):
                        if path.is_symlink() or not path.resolve().is_relative_to(attempt):raise ValueError('Unsafe scratch file path.')
                        path.unlink(missing_ok=True)
                # Derived trade indexes can be large after a killed worker. The
                # immutable journal, rather than this private SQLite file, owns
                # every durable trade and is the source for rebuilding it.
                for path in attempt.glob('openpine-trades-*'):
                    if not re.fullmatch('openpine-trades-[a-z0-9_]{8}',path.name):continue
                    if path.is_symlink() or not path.resolve().is_relative_to(attempt):raise ValueError('Unsafe trade-index scratch path.')
                    shutil.rmtree(path)
            except OSError:pending=True  # Scanners may briefly retain closed files.
            finally:lease.close()
        return pending
