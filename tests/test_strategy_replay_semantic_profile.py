from __future__ import annotations

import inspect

from openpine.gateway.routes import strategies


def test_strategy_replay_admits_semantic_profile() -> None:
    source = inspect.getsource(strategies.strategy_replay)
    assert "admit_semantic_profile" in source
    assert "semantic_profile=" in source
    assert "source=\"backtest\"" in source


def test_strategy_replay_persists_admitted_job() -> None:
    source = inspect.getsource(strategies.strategy_replay)
    assert "persist_gateway_job" in source
    assert "require_http_admit" in source
    assert 'kind="backtest"' in source
    assert "semantic_profile=" in source
