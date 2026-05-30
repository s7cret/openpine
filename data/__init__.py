"""Data layer — DataOrchestrator, DataPlanner, DataPlan, requirements.

Section 5.5, 5.7, 5.8, 5.9 of OpenPine TZ v3.
Section OP-DL-004: Candle Data Lake.
"""

from __future__ import annotations

from marketdata_provider.contracts import BarQuery
from openpine.data.contracts import WriteMode
from openpine.data.orchestrator import (
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
from openpine.data.provider_adapter import (
    create_local_marketdata_provider_adapter,
    ensure_marketdata_provider_version,
    normalize_provider_bar,
)

__all__ = [
    # Core classes (OP-DL-004)
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
    "MarketDataProvider",
    "create_local_marketdata_provider_adapter",
    "ensure_marketdata_provider_version",
    "normalize_provider_bar",
]
