"""Fail-fast weighted admission for coordinator work; no unbounded wait queue."""

import os
import threading
from functools import wraps
from contextlib import contextmanager

from packages.contracts.storage import CoordinatorPolicy
from packages.execution.preparation import invalid
from packages.execution.resources import resident_bytes


class WorkLimiter:
    def __init__(self, policy=None):
        self.policy = policy or CoordinatorPolicy()
        self.lock = threading.Lock()
        self.active = {"prepare": 0, "read": 0}
        self.reserved_mb = 0
        self.local = threading.local()

    def healthy(self):
        return resident_bytes(os.getpid()) <= self.policy.memory_mb * 1024**2

    @contextmanager
    def slot(self, kind):
        weight = self.policy.preparation_mb if kind == "prepare" else self.policy.read_mb
        count = self.policy.preparations if kind == "prepare" else self.policy.readers
        held = getattr(self.local,"weight",0)
        if held:
            if held < weight:
                invalid("A read reservation cannot be upgraded during work.", "COORDINATOR_BUSY")
            yield
            return
        with self.lock:
            if (self.active[kind] >= count or not self.healthy()
                    or self.policy.base_mb + self.reserved_mb + weight > self.policy.memory_mb):
                invalid("Coordinator work budget is busy; retry after current work completes.", "COORDINATOR_BUSY")
            self.active[kind] += 1
            self.reserved_mb += weight
            self.local.weight = weight
        try:
            yield
        finally:
            with self.lock:
                self.active[kind] -= 1
                self.reserved_mb -= weight
                self.local.weight = 0


def bounded(kind):
    def decorate(method):
        @wraps(method)
        def call(self,*args,**kwargs):
            with self.limiter.slot(kind):
                return method(self,*args,**kwargs)
        return call
    return decorate
