"""One job's charged history memory, including live preview/restore owners."""
import weakref
from packages.contracts.history_storage import CELL_BYTES
from packages.runtime.series import KernelError

class HistoryAccount:
    def __init__(self,limit_bytes=256*1024**2):
        self.limit_bytes=limit_bytes;self.used_bytes=0;self.peak_bytes=0
    def reserve(self,n,label):
        if self.used_bytes+n>self.limit_bytes:
            raise KernelError('HISTORY_MEMORY',f'History memory budget exceeded at {label}: {self.used_bytes+n} charged bytes, limit {self.limit_bytes}.','rule.history')
        self.used_bytes+=n;self.peak_bytes=max(self.peak_bytes,self.used_bytes)
    def release(self,n):self.used_bytes-=n

class Allocation:
    def __init__(self,account):
        self.account=account;self.bytes=[0]
        self.finalizer=weakref.finalize(self,self.release,account,self.bytes)
    @staticmethod
    def release(account,amount):account.release(amount[0])
    def reserve(self,cells,label):
        n=cells*CELL_BYTES;self.account.reserve(n,label);self.bytes[0]+=n
    def close(self):self.finalizer()
