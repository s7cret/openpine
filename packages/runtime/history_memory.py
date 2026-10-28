"""One job's charged history memory, including live preview/restore owners."""
import weakref
from packages.contracts.memory import charge
from packages.runtime.series import KernelError

class HistoryAccount:
    def __init__(self,limit_bytes=256*1024**2):
        self.limit_bytes=limit_bytes;self.used_bytes=0;self.peak_bytes=0;self.owners={}
    def check(self,n,label,kind='value'):
        if n<0:raise ValueError('Negative allocation.')
        if self.used_bytes+n>self.limit_bytes:
            raise KernelError('HISTORY_MEMORY',f'Job storage memory budget exceeded at {label} ({kind}): {self.used_bytes+n} charged bytes, limit {self.limit_bytes}; process RSS is a separate guard.','rule.history')
    def reserve(self,n,label,kind='value'):
        if n==0:return
        self.check(n,label,kind)
        self.used_bytes+=n;self.peak_bytes=max(self.peak_bytes,self.used_bytes)
        key=(label,kind);self.owners[key]=self.owners.get(key,0)+n
    def release(self,n,label,kind):
        self.used_bytes-=n;key=(label,kind);left=self.owners[key]-n
        if left:self.owners[key]=left
        else:del self.owners[key]
    def snapshot(self):
        return dict(charged_bytes=self.used_bytes,peak_charged_bytes=self.peak_bytes,limit_bytes=self.limit_bytes,
            charge_is_rss=False,owners=[dict(owner=k[0],kind=k[1],charged_bytes=n) for k,n in sorted(self.owners.items())])

class Allocation:
    def __init__(self,account):
        self.account=account;self.bytes=[0];self.entries={}
        self.finalizer=weakref.finalize(self,self.release,account,self.bytes,self.entries)
    @staticmethod
    def release(account,amount,entries):
        for (label,kind),n in entries.items():account.release(n,label,kind)
        entries.clear();amount[0]=0
    def reserve(self,cells,label,kind='value'):
        if not self.finalizer.alive:raise RuntimeError('Allocation used after close: '+label)
        if cells==0:return
        n=charge(kind,cells);self.account.reserve(n,label,kind);self.bytes[0]+=n
        key=(label,kind);self.entries[key]=self.entries.get(key,0)+n
    def transfer(self,target):
        assert self.account is target.account
        target.bytes[0]+=self.bytes[0];self.bytes[0]=0
        for key,n in self.entries.items():target.entries[key]=target.entries.get(key,0)+n
        self.entries.clear()
    def resize(self,cells,label,kind):
        target=charge(kind,cells);key=(label,kind);current=self.entries.get(key,0)
        if target>current:self.reserve((target-current)//charge(kind),label,kind)
        elif target<current:
            self.account.release(current-target,label,kind);self.bytes[0]-=current-target
            if target:self.entries[key]=target
            else:self.entries.pop(key,None)
    def discard_resize_debt(self):
        for (label,kind),n in list(self.entries.items()):
            if label.endswith(' resize'):
                self.account.release(n,label,kind);self.bytes[0]-=n;del self.entries[label,kind]
    def close(self):self.finalizer()
