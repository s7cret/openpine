"""Retryable free-space admission is shared without a storage dependency."""
import errno

class DiskBusy(OSError):
    def __init__(self,decision):
        super().__init__(errno.ENOSPC,'Free disk budget is busy; committed objects remain intact.')
        self.admission=decision
