"""Snapshot policy — section 33.7, 30.6."""

from __future__ import annotations

from dataclasses import dataclass

from openpine.state.store import SavePolicy


@dataclass
class SnapshotPolicy:
    """Section 33.7: unified snapshot policy configuration.

    Default live/paper: state.save_policy=every_bar and save_interval_bars=1.
    Failed process_next_bar never creates a new snapshot (section 33.2).
    """
    save_policy: SavePolicy = SavePolicy.EVERY_BAR
    save_interval_bars: int = 1
    max_snapshots_per_strategy: int = 10

    def should_save(self, bars_since_last: int, failed_bar: bool = False) -> bool:
        """Determine if snapshot should be saved.

        Returns False for failed_bar (section 33.2).
        When save_policy is INTERVAL, saves every N bars.
        When save_policy is EVERY_BAR, always saves on non-failed bars.
        """
        if failed_bar:
            return False
        if self.save_policy == SavePolicy.EVERY_BAR:
            return True
        if self.save_policy == SavePolicy.INTERVAL:
            return bars_since_last >= self.save_interval_bars
        # ON_REQUEST: never auto-save
        return False
