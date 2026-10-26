"""Fail-fast weighted admission for coordinator work; no unbounded wait queue."""

import os
import threading
from functools import wraps
from contextlib import contextmanager

from packages.contracts.storage import CoordinatorPolicy
from packages.execution.resources import resident_bytes
from packages.contracts import Diagnostic,OpenPineError


class CoordinatorBusy(OpenPineError):
    def __init__(self,reason,kind,active,reserved_mb,retry_after_ms=100):
        super().__init__(Diagnostic('INPUT_INVALID','COORDINATOR_BUSY','Coordinator work budget is busy; retry after current work completes.'))
        self.admission={'schema_version':'openpine.admission.1','reason':reason,'kind':kind,
            'active':dict(active),'reserved_mb':reserved_mb,'retryable':True,'retry_after_ms':retry_after_ms}


class WorkLimiter:
    def __init__(self, policy=None):
        self.policy = policy or CoordinatorPolicy()
        self.lock = threading.Lock()
        self.active = {"prepare": 0, "read": 0, "metadata": 0}
        self.reserved_mb = 0
        self.local = threading.local()

    def healthy(self):
        return resident_bytes(os.getpid()) <= self.policy.memory_mb * 1024**2

    @contextmanager
    def slot(self, kind):
        if kind not in self.active:raise ValueError('Unknown coordinator work class.')
        # Two small catalog-only operations use the already reserved base_mb.
        # This class must never open result, IR, candle or export files.
        weight = self.policy.preparation_mb if kind == "prepare" else 0 if kind=='metadata' else self.policy.read_mb
        count = self.policy.preparations if kind == "prepare" else 2 if kind=='metadata' else self.policy.readers
        held = getattr(self.local,"weight",0)
        if getattr(self.local,'kind',None) is not None:
            if held < weight:
                raise CoordinatorBusy('reservation_upgrade',kind,self.active,self.reserved_mb)
            yield
            return
        with self.lock:
            reason=('slot_limit' if self.active[kind]>=count else 'resident_memory' if not self.healthy() else
                'memory_reservation' if self.policy.base_mb+self.reserved_mb+weight>self.policy.memory_mb else None)
            if reason:raise CoordinatorBusy(reason,kind,self.active,self.reserved_mb)
            self.active[kind] += 1
            self.reserved_mb += weight
            self.local.weight = weight
            self.local.kind = kind
        try:
            yield
        finally:
            with self.lock:
                self.active[kind] -= 1
                self.reserved_mb -= weight
                self.local.weight = 0
                self.local.kind = None


def bounded(kind):
    def decorate(method):
        @wraps(method)
        def call(self,*args,**kwargs):
            with self.limiter.slot(kind):
                return method(self,*args,**kwargs)
        return call
    return decorate
