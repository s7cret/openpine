"""Shared admission leases cover work which has not materialized in RSS yet."""
import threading,time
from packages.contracts.host_memory import HostMemoryPolicy,MIB
from .resources import host_memory

class HostAdmission:
    def __init__(self,policy=None,probe=None,*,clock=time.monotonic):
        self.policy=policy or HostMemoryPolicy();self.probe=probe or host_memory;self.clock=clock
        self.lock=threading.RLock();self.leases={};self.pressured=False;self.sample=None;self.sample_time=-1.

    def _sample(self):
        now=self.clock()
        if self.sample_time<0 or now-self.sample_time>=.25:
            value=self.probe()
            if value is not None and (type(value) is not dict
                    or any(type(value.get(k)) is not int for k in ('total_bytes','available_bytes'))
                    or not 0<=value['available_bytes']<=value['total_bytes'] or value['total_bytes']<1
                    or type(value.get('source')) is not str):value=None
            self.sample,self.sample_time=value,now
        return self.sample

    def snapshot(self):
        with self.lock:
            sample=self._sample();reserved=sum(self.leases.values())
            return dict(schema_version='openpine.host-memory.1',metric_status='available' if sample else 'unavailable',
                **(sample or dict(total_bytes=None,available_bytes=None,source='unavailable')),
                os_reserve_bytes=self.policy.reserve_bytes(sample['total_bytes']) if sample else None,
                pending_reservation_bytes=reserved,resume_margin_bytes=self.policy.resume_margin_mb*MIB,
                pressured=self.pressured,fallback='configured_limits_and_process_RSS')

    def reserve(self,token,amount):
        if type(token) is not str or not token or type(amount) is not int or amount<0:raise ValueError('Invalid host reservation.')
        with self.lock:
            if token in self.leases:return None
            sample=self._sample()
            if sample:
                needed=self.policy.reserve_bytes(sample['total_bytes'])+sum(self.leases.values())+amount
                if self.pressured:needed+=self.policy.resume_margin_mb*MIB
                if sample['available_bytes']<needed:
                    self.pressured=True
                    return dict(**self.snapshot(),required_available_bytes=needed,requested_bytes=amount,reason='host_memory',retryable=True)
            self.pressured=False;self.leases[token]=amount
            return None

    def materialized(self,token,resident,limit):
        with self.lock:
            if token in self.leases:self.leases[token]=max(0,limit-resident)

    def release(self,token):
        with self.lock:self.leases.pop(token,None)
