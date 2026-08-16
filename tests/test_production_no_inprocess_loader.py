from __future__ import annotations

from pathlib import Path

import pytest

from openpine.runtime.engine import (
    BacktestArtifactError,
    load_generated_class_from_artifact,
    load_strategy_class_from_artifact,
)

PKG = Path(__file__).resolve().parents[1] / "openpine"


def test_production_loaders_reject_in_process_flag() -> None:
    with pytest.raises(BacktestArtifactError, match="in-process"):
        load_generated_class_from_artifact("src", "art", unsafe_in_process=True)
    with pytest.raises(BacktestArtifactError, match="in-process"):
        load_strategy_class_from_artifact(
            "src", "art", symbol="BTCUSDT", timeframe="1m", unsafe_in_process=True
        )


def test_production_source_never_enables_in_process_import() -> None:
    offenders: list[str] = []
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "unsafe_in_process=True" in text:
            offenders.append(str(path.relative_to(PKG)))
    assert offenders == []
