"""Validated execution progress; it never implies a result has been accepted."""
from __future__ import annotations

import time
from collections.abc import Callable


class ProgressError(ValueError):
    pass


class ProgressReporter:
    """Keep counters monotonic and throttle UI/IPC updates without hiding failures."""

    def __init__(self, callback: Callable[[int, int], None] | None, *, max_total: int,
                 interval: float = 0.25, clock: Callable[[], float] = time.monotonic) -> None:
        if type(max_total) is not int or max_total < 0 or not 0 <= interval <= 60:
            raise ProgressError("invalid progress limits")
        self.callback, self.max_total = callback, max_total
        self.interval, self.clock = interval, clock
        self.total: int | None = None
        self.done = 0
        self.last_emit: tuple[int, int] | None = None
        self.last_time = float("-inf")

    def report(self, done: int, total: int, *, force: bool = False) -> None:
        if (type(done) is not int or type(total) is not int
                or not 0 <= self.done <= done <= total <= self.max_total
                or (self.total is not None and total != self.total)):
            raise ProgressError("invalid or regressing execution progress")
        self.total, self.done = total, done
        value, now = (done, total), self.clock()
        if value == self.last_emit:
            return
        if force or done == total or now - self.last_time >= self.interval:
            if self.callback is not None:
                self.callback(done, total)
            self.last_time, self.last_emit = now, value
