from pathlib import Path

import pytest

from openpine.live_preview import (
    LiveConfirmError,
    make_live_preview,
    require_live_confirmation,
)
from openpine.runtime.cgroup import CgroupError, apply_memory_max


def test_preview_hash_is_stable_and_required() -> None:
    preview = make_live_preview("strat-1", now_ms=1_000)
    require_live_confirmation(
        strategy_id="strat-1",
        preview_hash_value=preview["preview_hash"],
        confirmation="LIVE",
        expires_at_utc_ms=preview["expires_at_utc_ms"],
        now_ms=1_000,
    )
    with pytest.raises(LiveConfirmError, match="LIVE"):
        require_live_confirmation(
            strategy_id="strat-1",
            preview_hash_value=preview["preview_hash"],
            confirmation="yes",
            expires_at_utc_ms=preview["expires_at_utc_ms"],
            now_ms=1_000,
        )
    with pytest.raises(LiveConfirmError, match="expired"):
        require_live_confirmation(
            strategy_id="strat-1",
            preview_hash_value=preview["preview_hash"],
            confirmation="LIVE",
            expires_at_utc_ms=preview["expires_at_utc_ms"],
            now_ms=preview["expires_at_utc_ms"],
        )
    with pytest.raises(LiveConfirmError, match="mismatch"):
        require_live_confirmation(
            strategy_id="other",
            preview_hash_value=preview["preview_hash"],
            confirmation="LIVE",
            expires_at_utc_ms=preview["expires_at_utc_ms"],
            now_ms=1_000,
        )


def test_cgroup_memory_max_is_written(tmp_path: Path) -> None:
    (tmp_path / "memory.max").write_text("max\n", encoding="ascii")
    apply_memory_max(tmp_path, memory_max=64)
    assert (tmp_path / "memory.max").read_text(encoding="ascii").strip() == "64"
    with pytest.raises(CgroupError):
        apply_memory_max(tmp_path / "missing")
