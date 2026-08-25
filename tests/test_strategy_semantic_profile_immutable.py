from __future__ import annotations

from types import SimpleNamespace

import pytest

from openpine.admission import admit_strategy_semantic_profile
from openpine_contracts import AdmitError


def test_stored_legacy_strategy_keeps_legacy_semantics_without_user_override() -> None:
    strategy = SimpleNamespace(semantic_profile="legacy_4x")

    admitted = admit_strategy_semantic_profile(strategy, source="paper")

    assert admitted.value == "legacy_4x"


def test_request_cannot_override_strategy_semantic_profile() -> None:
    strategy = SimpleNamespace(semantic_profile="strict_5x")

    with pytest.raises(AdmitError) as exc_info:
        admit_strategy_semantic_profile(
            strategy,
            source="backtest",
            requested_profile="legacy_4x",
        )

    assert exc_info.value.code == "SEMANTIC_PROFILE_MISMATCH"
