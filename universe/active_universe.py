"""ActiveUniverse — tracks active strategies and their data requirements.

Section 5.6 of OpenPine TZ v3.

OpenPine must:
1. Take all enabled StrategyInstance in modes paper/live/replay/backtest jobs.
2. Load their CompileArtifact and requirements.json.
3. Build DataPlan/FeaturePlan for each strategy.
4. Merge identical requirements.
5. Create unique DataRequirement/AggregationRequirement/FeatureRequirement.
6. Create deduped jobs.
7. Save mapping requirement_id → required_by_strategy_ids.
"""

from __future__ import annotations

import structlog
from typing import Optional

from openpine.data.planner import (
    AggregationRequirement,
    DataPlan,
    DataRequirement,
    FeatureRequirement,
)

log = structlog.get_logger(__name__)


class ActiveUniverse:
    """Section 5.6: tracks active strategies and their data requirements.

    Aggregates data requirements from all enabled strategies.
    Provides deduplicated merged requirements and DataPlan building.
    """

    def __init__(self) -> None:
        """Initialize the ActiveUniverse."""
        self._strategy_requirements: dict[str, list[DataRequirement]] = {}
        self._strategy_aggregation_requirements: dict[str, list[AggregationRequirement]] = {}
        self._strategy_feature_requirements: dict[str, list[FeatureRequirement]] = {}
        self._mode: str = "active"

    def add_strategy(self, strategy_id: str, requirements: list[DataRequirement]) -> None:
        """Add a strategy and its data requirements.

        Args:
            strategy_id: Unique strategy identifier.
            requirements: List of DataRequirements for this strategy.
        """
        if strategy_id in self._strategy_requirements:
            log.warning("active_universe.strategy_already_exists", strategy_id=strategy_id)
            return

        self._strategy_requirements[strategy_id] = list(requirements)
        log.info("active_universe.strategy_added", strategy_id=strategy_id, req_count=len(requirements))

    def remove_strategy(self, strategy_id: str) -> None:
        """Remove a strategy and its requirements.

        Args:
            strategy_id: Strategy to remove.
        """
        if strategy_id not in self._strategy_requirements:
            log.warning("active_universe.strategy_not_found", strategy_id=strategy_id)
            return

        del self._strategy_requirements[strategy_id]
        self._strategy_aggregation_requirements.pop(strategy_id, None)
        self._strategy_feature_requirements.pop(strategy_id, None)
        log.info("active_universe.strategy_removed", strategy_id=strategy_id)

    def add_strategy_aggregation(
        self, strategy_id: str, requirements: list[AggregationRequirement]
    ) -> None:
        """Add aggregation requirements for a strategy.

        Args:
            strategy_id: Strategy identifier.
            requirements: List of AggregationRequirements.
        """
        if strategy_id not in self._strategy_requirements:
            log.warning("active_universe.strategy_not_found_for_agg", strategy_id=strategy_id)
            return
        self._strategy_aggregation_requirements[strategy_id] = list(requirements)

    def add_strategy_feature(
        self, strategy_id: str, requirements: list[FeatureRequirement]
    ) -> None:
        """Add feature requirements for a strategy.

        Args:
            strategy_id: Strategy identifier.
            requirements: List of FeatureRequirements.
        """
        if strategy_id not in self._strategy_requirements:
            log.warning("active_universe.strategy_not_found_for_feat", strategy_id=strategy_id)
            return
        self._strategy_feature_requirements[strategy_id] = list(requirements)

    def get_strategy_requirements(self, strategy_id: str) -> list[DataRequirement]:
        """Get data requirements for a specific strategy.

        Args:
            strategy_id: Strategy identifier.

        Returns:
            List of DataRequirements for the strategy.
        """
        return list(self._strategy_requirements.get(strategy_id, []))

    def get_strategy_aggregation_requirements(
        self, strategy_id: str
    ) -> list[AggregationRequirement]:
        """Get aggregation requirements for a specific strategy.

        Args:
            strategy_id: Strategy identifier.

        Returns:
            List of AggregationRequirements for the strategy.
        """
        return list(self._strategy_aggregation_requirements.get(strategy_id, []))

    def get_strategy_feature_requirements(self, strategy_id: str) -> list[FeatureRequirement]:
        """Get feature requirements for a specific strategy.

        Args:
            strategy_id: Strategy identifier.

        Returns:
            List of FeatureRequirements for the strategy.
        """
        return list(self._strategy_feature_requirements.get(strategy_id, []))

    def get_merged_requirements(self) -> list[DataRequirement]:
        """Get deduplicated merged data requirements across all strategies.

        Requirements with the same dedupe_key are merged into one.

        Returns:
            List of unique DataRequirements.
        """
        all_reqs: list[DataRequirement] = []
        for reqs in self._strategy_requirements.values():
            all_reqs.extend(reqs)

        # Deduplicate
        seen_keys: set[str] = set()
        unique: list[DataRequirement] = []
        for req in all_reqs:
            key = req.dedupe_key
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(req)

        log.info(
            "active_universe.merged_requirements",
            total=len(all_reqs),
            unique=len(unique),
            strategy_count=len(self._strategy_requirements),
        )
        return unique

    def get_merged_aggregation_requirements(self) -> list[AggregationRequirement]:
        """Get deduplicated merged aggregation requirements across all strategies.

        Returns:
            List of unique AggregationRequirements.
        """
        all_reqs: list[AggregationRequirement] = []
        for reqs in self._strategy_aggregation_requirements.values():
            all_reqs.extend(reqs)

        seen_keys: set[str] = set()
        unique: list[AggregationRequirement] = []
        for req in all_reqs:
            key = req.dedupe_key
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(req)

        return unique

    def get_merged_feature_requirements(self) -> list[FeatureRequirement]:
        """Get deduplicated merged feature requirements across all strategies.

        Returns:
            List of unique FeatureRequirements.
        """
        all_reqs: list[FeatureRequirement] = []
        for reqs in self._strategy_feature_requirements.values():
            all_reqs.extend(reqs)

        seen_keys: set[str] = set()
        unique: list[FeatureRequirement] = []
        for req in all_reqs:
            key = req.dedupe_key
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(req)

        return unique

    def build_data_plan(self) -> DataPlan:
        """Build a DataPlan with deduplicated requirements from all strategies.

        Returns:
            DataPlan containing all unique requirements.
        """
        plan = DataPlan(
            requirements=self.get_merged_requirements(),
            aggregation_requirements=self.get_merged_aggregation_requirements(),
            feature_requirements=self.get_merged_feature_requirements(),
        )

        # Explicit deduplication
        plan = plan.deduplicate()

        log.info(
            "active_universe.data_plan_built",
            data_reqs=len(plan.requirements),
            agg_reqs=len(plan.aggregation_requirements),
            feat_reqs=len(plan.feature_requirements),
        )
        return plan

    def list_strategies(self) -> list[str]:
        """List all strategy IDs in the universe.

        Returns:
            List of strategy IDs.
        """
        return list(self._strategy_requirements.keys())

    @property
    def mode(self) -> str:
        """Return universe mode."""
        return self._mode

    def __len__(self) -> int:
        """Return number of strategies in universe."""
        return len(self._strategy_requirements)


__all__ = [
    "ActiveUniverse",
]
