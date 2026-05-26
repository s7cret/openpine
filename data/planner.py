"""Data planning — DataPlan, DataRequirement, AggregationRequirement, FeatureRequirement.

Section 5.5, 5.7, 5.8, 5.9 of OpenPine TZ v3.
"""

from __future__ import annotations

import hashlib
from typing import Optional

import pydantic

from openpine.contracts import InstrumentKey as ContractInstrumentKey
from openpine.contracts import Timeframe as ContractTimeframe


class DataRequirement(pydantic.BaseModel):
    """Data requirement for a strategy — section 5.7.

    Represents a need to load/store OHLCV bars for an instrument/timeframe/range.
    """

    instrument_key: str  # e.g. "binance:usdm:BTCUSDT:trade"
    timeframe: str  # e.g. "1m", "5m", "15m"
    start_ms: int  # UTC epoch ms inclusive
    end_ms: int  # UTC epoch ms inclusive
    provider: str = "binance"
    reason: Optional[str] = None

    @property
    def dedupe_key(self) -> str:
        """Unique dedupe key for this requirement.

        Format: DATA:<exchange>/<market_type>/<symbol>/<price_type>/<timeframe>/<provider>/<start_ms>/<end_ms>
        """
        return f"DATA:{self.instrument_key}/{self.timeframe}/{self.provider}/{self.start_ms}/{self.end_ms}"

    def __hash__(self) -> int:
        return hash(self.dedupe_key)


class AggregationRequirement(pydantic.BaseModel):
    """OHLCV aggregation requirement — section 5.8.

    Represents a need to aggregate from a lower source timeframe to a higher target timeframe.
    Owned by AggregationWorkerPool (section 33.8).
    """

    instrument_key: str  # e.g. "binance:usdm:BTCUSDT:trade"
    source_timeframe: str  # e.g. "1m"
    target_timeframe: str  # e.g. "5m"
    start_ms: int  # UTC epoch ms inclusive
    end_ms: int  # UTC epoch ms inclusive

    @property
    def dedupe_key(self) -> str:
        """Unique dedupe key for aggregation.

        Format: AGG:<instrument_key>/<source_timeframe>/<target_timeframe>/<start_ms>/<end_ms>
        """
        return f"AGG:{self.instrument_key}/{self.source_timeframe}/{self.target_timeframe}/{self.start_ms}/{self.end_ms}"

    def __hash__(self) -> int:
        return hash(self.dedupe_key)


class FeatureRequirement(pydantic.BaseModel):
    """Feature computation requirement — section 5.9.

    Represents a need to compute an indicator/feature on available candles.
    Owned by FeatureWorkerPool (section 33.8).
    """

    instrument_key: str  # e.g. "binance:usdm:BTCUSDT:trade"
    timeframe: str  # e.g. "15m"
    expression_key: str  # e.g. "EMA(close,5000)" or "RSI(close,14)"
    feature_key_hash: str  # Hash of full feature key
    start_ms: int  # UTC epoch ms inclusive
    end_ms: int  # UTC epoch ms inclusive
    requires: list[DataRequirement | AggregationRequirement] = pydantic.Field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        """Unique dedupe key for feature.

        Format: FEATURE:<feature_key_hash>/<start_ms>/<end_ms>
        """
        return f"FEATURE:{self.feature_key_hash}/{self.start_ms}/{self.end_ms}"

    def __hash__(self) -> int:
        return hash(self.dedupe_key)


class DataPlan(pydantic.BaseModel):
    """Data plan containing all requirements for the active universe — section 5.5.

    Built by ActiveUniverse from all enabled strategies.
    Deduplicated to avoid duplicate work.
    """

    requirements: list[DataRequirement] = pydantic.Field(default_factory=list)
    aggregation_requirements: list[AggregationRequirement] = pydantic.Field(default_factory=list)
    feature_requirements: list[FeatureRequirement] = pydantic.Field(default_factory=list)

    def deduplicate(self) -> DataPlan:
        """Remove duplicate requirements by instrument+timeframe+range.

        Uses dedupe_key to identify unique requirements.
        """
        # Deduplicate data requirements
        seen_data_keys: set[str] = set()
        unique_data: list[DataRequirement] = []
        for req in self.requirements:
            key = req.dedupe_key
            if key not in seen_data_keys:
                seen_data_keys.add(key)
                unique_data.append(req)

        # Deduplicate aggregation requirements
        seen_agg_keys: set[str] = set()
        unique_agg: list[AggregationRequirement] = []
        for req in self.aggregation_requirements:
            key = req.dedupe_key
            if key not in seen_agg_keys:
                seen_agg_keys.add(key)
                unique_agg.append(req)

        # Deduplicate feature requirements
        seen_feat_keys: set[str] = set()
        unique_feat: list[FeatureRequirement] = []
        for req in self.feature_requirements:
            key = req.dedupe_key
            if key not in seen_feat_keys:
                seen_feat_keys.add(key)
                unique_feat.append(req)

        return DataPlan(
            requirements=unique_data,
            aggregation_requirements=unique_agg,
            feature_requirements=unique_feat,
        )

    def add_requirement(self, req: DataRequirement) -> None:
        """Add a data requirement if not duplicate."""
        if req.dedupe_key not in {r.dedupe_key for r in self.requirements}:
            self.requirements.append(req)

    def add_aggregation_requirement(self, req: AggregationRequirement) -> None:
        """Add an aggregation requirement if not duplicate."""
        if req.dedupe_key not in {r.dedupe_key for r in self.aggregation_requirements}:
            self.aggregation_requirements.append(req)

    def add_feature_requirement(self, req: FeatureRequirement) -> None:
        """Add a feature requirement if not duplicate."""
        if req.dedupe_key not in {r.dedupe_key for r in self.feature_requirements}:
            self.feature_requirements.append(req)


class DataPlanner:
    """Coordinates data planning across ActiveUniverse.

    Responsible for building DataPlan from ActiveUniverse and
    providing instrument listing for data planning.
    """

    def __init__(
        self,
        stream_manager,  # MarketDataStreamManager
        orchestrator,     # DataOrchestrator
        universe,         # ActiveUniverse
    ) -> None:
        self._stream_manager = stream_manager
        self._orchestrator = orchestrator
        self._universe = universe

    def list_available_instruments(self) -> list[str]:
        """List available instruments from the active universe."""
        return []  # Placeholder: would query stream_manager for available data

    def plan_for_strategy(self, strategy_id: str) -> DataPlan:
        """Build data plan for a specific strategy."""
        return self._universe.build_data_plan()


__all__ = [
    "DataRequirement",
    "AggregationRequirement",
    "FeatureRequirement",
    "DataPlan",
    "DataPlanner",
]
