"""Data layer — DataOrchestrator, DataPlanner, DataPlan, requirements.

Section 5.5, 5.7, 5.8, 5.9 of OpenPine TZ v3.
Section OP-DL-004: Candle Data Lake.
"""

from __future__ import annotations

from openpine.data.bar_query import BarQuery
from openpine.data.candle_storage import CandleStorage
from openpine.data.contracts import WriteMode
from openpine.data.data_orchestrator import (
    DataOrchestrator,
    MarketDataProvider,
)
from openpine.data.models import (
    AggregationRequirement,
    CandleCommitResult,
    CandleManifest,
    DataGap,
    DataPlan,
    DataRequirement,
    EnsureDataResult,
    FeaturePlan,
    WriteResult,
)
from openpine.data.orchestrator import (
    DataOrchestrator as LegacyDataOrchestrator,
    MarketDataProvider as LegacyMarketDataProvider,
)
from openpine.data.provider_adapter import (
    LocalMarketDataProviderAdapter,
    LocalProviderInstallation,
    create_local_marketdata_provider_adapter,
    detect_local_marketdata_provider,
    normalize_provider_bar,
)
from openpine.data.planner import (
    AggregationRequirement as LegacyAggregationRequirement,
    DataPlan as LegacyDataPlan,
    DataPlanner,
    DataRequirement as LegacyDataRequirement,
    FeatureRequirement,
)

__all__ = [
    # Core classes (OP-DL-004)
    "CandleStorage",
    "DataOrchestrator",
    # Query
    "BarQuery",
    # Models (OP-DL-004)
    "CandleManifest",
    "DataRequirement",
    "AggregationRequirement",
    "DataGap",
    "DataPlan",
    "FeaturePlan",
    "WriteResult",
    "EnsureDataResult",
    "CandleCommitResult",
    # Enums
    "WriteMode",
    # Legacy (existing)
    "LegacyDataOrchestrator",
    "LegacyMarketDataProvider",
    "LegacyDataRequirement",
    "LegacyAggregationRequirement",
    "LegacyDataPlan",
    "DataPlanner",
    "FeatureRequirement",
    "MarketDataProvider",
    "LocalMarketDataProviderAdapter",
    "LocalProviderInstallation",
    "create_local_marketdata_provider_adapter",
    "detect_local_marketdata_provider",
    "normalize_provider_bar",
]
