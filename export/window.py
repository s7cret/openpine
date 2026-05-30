"""Export window contract and timestamp parsing."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class ExportWindow:
    from_ms: int
    to_ms: int

    def __post_init__(self) -> None:
        if self.from_ms >= self.to_ms:
            raise ValueError("export window from_ms must be less than to_ms")

    def contains(self, ts_ms: int | None) -> bool:
        return ts_ms is not None and self.from_ms <= ts_ms < self.to_ms


def parse_time_ms(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    if value.isdigit():
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return int(timestamp.timestamp() * 1000)
