"""Causal callback cursor shared by bulk and interactive Pine execution."""
from __future__ import annotations

from dataclasses import dataclass
from openpine_contracts import ExecutionEvent
from pinelib.runtime.metadata import BarValues


@dataclass
class ExecutionCursor:
    last: ExecutionEvent | None = None
    open_bar: int | None = None

    def validate(self, event: ExecutionEvent, values: BarValues) -> None:
        if not isinstance(event, ExecutionEvent):
            raise ValueError("an admitted execution event is required")
        if event.bar_open_time_utc_ms != values.time:
            raise ValueError("execution event does not match bar time")
        if self.last is None:
            if event.sequence != 0 or event.recalc_iteration != 0:
                raise ValueError("initial callback sequence is invalid")
            return
        old = self.last
        if event.sequence != old.sequence + 1:
            raise ValueError("callback sequence must be contiguous")
        if event.bar_index == old.bar_index:
            if (self.open_bar != event.bar_index
                    or event.bar_open_time_utc_ms != old.bar_open_time_utc_ms
                    or event.recalc_iteration != old.recalc_iteration + 1
                    or event.tick_index < old.tick_index):
                raise ValueError("recalculation does not match the provisional bar")
        elif (self.open_bar is not None or event.bar_index != old.bar_index + 1
              or event.bar_open_time_utc_ms <= old.bar_open_time_utc_ms
              or event.recalc_iteration != 0):
            raise ValueError("new callback requires the previous bar to be committed")
        if not event.realtime and (event.last_bar_index != old.last_bar_index
                or event.last_historical_bar_index != old.last_historical_bar_index):
            raise ValueError("historical dataset bounds changed during the run")

    def accept(self, event: ExecutionEvent) -> None:
        self.last = event
        self.open_bar = event.bar_index

    def require_commit(self, bar_index: int) -> None:
        if type(bar_index) is not int or self.open_bar != bar_index:
            raise ValueError("commit does not match the provisional bar")

    def finish(self, bar_index: int) -> None:
        self.require_commit(bar_index)
        self.open_bar = None
