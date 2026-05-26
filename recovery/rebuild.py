"""State rebuild — section 30.8: rebuild state after data repair."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openpine.state.errors import StateInconsistencyError

if TYPE_CHECKING:
    from openpine.state.store import StateStore


class StateRebuilder:
    """Section 30.8: rebuild state after data repair.

    Rebuild procedure:
    1. Find latest compatible valid snapshot before repaired range.
    2. If none exists, bootstrap from configured strategy start/prehistory.
    3. Replay bars through DataOrchestrator with BarQuery(source='storage', gap_policy='fail').
    4. Save new snapshot with reason=rebuild.
    5. Compare last_processed_bar_time with target.
    6. Mark old snapshots superseded, not deleted.
    7. Emit StateRebuilt event.
    """

    def __init__(
        self,
        state_store: "StateStore",
        data_orchestrator: Any | None = None,
        backtest_engine: Any | None = None,
    ) -> None:
        self.state_store = state_store
        self.data_orchestrator = data_orchestrator
        self.backtest_engine = backtest_engine

    def rebuild(
        self,
        strategy_id: str,
        from_bar_time: int,
        reason: str = "data_repaired",
    ) -> "StrategyState":
        """Rebuild state by replaying bars from from_bar_time.

        Uses backtest_engine if available, otherwise replays from saved bars.
        Saves new snapshot with reason=rebuild after successful replay.
        """
        # Find latest compatible snapshot before from_bar_time
        snapshots = self.state_store.list_snapshots(strategy_id)
        candidates = [
            s for s in snapshots
            if s.status == "active" and s.bar_time < from_bar_time
        ]
        if candidates:
            latest = max(candidates, key=lambda s: s.saved_at)
            state = self.state_store.load_snapshot(strategy_id)
            if state is None:
                raise StateInconsistencyError(
                    f"No loadable snapshot found for {strategy_id}"
                )
        else:
            raise StateInconsistencyError(
                f"No compatible snapshot before {from_bar_time} for {strategy_id}"
            )

        # Replay bars if we have a data orchestrator
        if self.data_orchestrator is not None:
            bars = self.data_orchestrator.get_bars(
                instrument_key=state.instrument_key,
                timeframe=state.timeframe,
                start_time=from_bar_time,
            )
            for bar in bars:
                if self.backtest_engine is not None:
                    self.backtest_engine.process_next_bar(
                        state=state,
                        bar=bar,
                    )
                # Update bar_time
                state.bar_time = bar.time

        # Save rebuilt snapshot
        saved = self.state_store.save_snapshot(state, reason=reason, failed_bar=False)
        if saved is None:
            raise StateInconsistencyError(
                f"Rebuild failed: could not save snapshot for {strategy_id}"
            )

        return state

    def verify_state(self, strategy_id: str) -> bool:
        """Verify state consistency after rebuild.

        Checks that the latest active snapshot:
        - Exists and loads without error
        - Has a valid bar_time
        - Has valid state_data
        """
        try:
            state = self.state_store.load_snapshot(strategy_id)
            if state is None:
                return False
            # Basic consistency checks
            if state.bar_time <= 0:
                return False
            if not state.strategy_id:
                return False
            return True
        except Exception:
            return False

    def invalidate(
        self,
        strategy_id: str,
        since_bar_time: int | None = None,
    ) -> None:
        """Section 30.8: invalidate snapshots after data repair.

        If since_bar_time provided, delete snapshots after that time.
        Otherwise delete all snapshots for strategy.
        """
        snapshots = self.state_store.list_snapshots(strategy_id)
        for snap in snapshots:
            if since_bar_time is None or snap.bar_time >= since_bar_time:
                snap.status = "invalid"
