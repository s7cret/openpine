"""Retryable free-space admission is shared without a storage dependency."""
import errno

class DiskBusy(OSError):
    def __init__(self,decision):
        super().__init__(errno.ENOSPC,'Free disk budget is busy; committed objects remain intact.')
        self.admission=decision

class StorageQuota(OSError):
    """A configured object cap is distinct from temporary free-space pressure."""
    retryable=False
    def __init__(self):
        super().__init__(errno.ENOSPC,'Immutable object quota exceeded; inspect/prune/collect or increase quota.')
