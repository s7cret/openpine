from __future__ import annotations

import pytest

from openpine.gateway.routes import backtest as routes


def test_owned_backtest_rejects_non_artifact_adapter() -> None:
    with pytest.raises(RuntimeError, match="stamped artifact"):
        routes._run_owned_backtest(
            "run-1",
            set(),
            object(),
            object,
            [],
            object(),
            {},
            None,
        )


def test_owned_backtest_requires_artifact_spec_not_class_path(monkeypatch) -> None:
    calls: list[object] = []

    def forbid(*args, **kwargs):
        calls.append("thread")
        raise AssertionError("class-path must not run")

    monkeypatch.setattr(routes, "_execute_backtest_run_in_thread", forbid)
    with pytest.raises(RuntimeError, match="stamped artifact"):
        routes._run_owned_backtest(
            "run-2",
            set(),
            object(),
            object,
            [],
            object(),
            {},
            None,
        )
    assert calls == []
