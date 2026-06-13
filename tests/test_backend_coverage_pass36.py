from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")


class FakeConn:
    def __init__(self):
        self.executed = []
    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self
    def commit(self):
        self.executed.append(("commit", ()))


class FakeStrategyRegistry:
    strategies: dict[str, SimpleNamespace] = {}

    def __init__(self):
        self._conn = FakeConn()
        self._mem = self.strategies
        self.closed = False
    def close(self):
        self.closed = True
    def list_strategies(self):
        return list(self.strategies.values())
    def get_strategy(self, strategy_id):
        if strategy_id not in self.strategies:
            raise KeyError(strategy_id)
        return self.strategies[strategy_id]
    def register_strategy(self, *, artifact_id, symbol, timeframe, params, name, pine_id, exchange, market_type, mode):
        sid = name or "strat_auto"
        s = SimpleNamespace(
            strategy_id=sid,
            id=sid,
            name=sid,
            pine_id=pine_id,
            artifact_id=artifact_id,
            params_hash="hash1",
            params_json="{}",
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            market_type=market_type,
            mode=mode,
            enabled=False,
            status="pending",
            created_at=0,
            updated_at=0,
        )
        self.strategies[sid] = s
        return s
    def update_status(self, strategy_id, status):
        self.get_strategy(strategy_id).status = status


class FakePineRegistry:
    sources: dict[str, SimpleNamespace] = {}
    def close(self):
        pass
    def get_source(self, name):
        if name not in self.sources:
            raise KeyError(name)
        return self.sources[name]


def _install_fake_registries(monkeypatch):
    registry_mod = ModuleType("openpine.registry")
    registry_mod.SQLiteStrategyRegistry = FakeStrategyRegistry
    strategies_mod = ModuleType("openpine.registry.strategies")
    strategies_mod._make_params_hash = lambda params: "hash_" + "_".join(sorted(params))
    pine_registry_mod = ModuleType("openpine.pine.registry")
    pine_registry_mod.SQLitePineSourceRegistry = FakePineRegistry
    monkeypatch.setitem(sys.modules, "openpine.registry", registry_mod)
    monkeypatch.setitem(sys.modules, "openpine.registry.strategies", strategies_mod)
    monkeypatch.setitem(sys.modules, "openpine.pine.registry", pine_registry_mod)


def _strategy(strategy_id="s1", status="paused"):
    return SimpleNamespace(
        strategy_id=strategy_id,
        id=strategy_id,
        name="Name",
        pine_id="pine1",
        artifact_id="art1",
        params_hash="h0",
        params_json='{"a": "1"}',
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        mode="paper",
        enabled=False,
        status=status,
        created_at=0,
        updated_at=0,
    )


def test_strategy_cli_lifecycle_happy_and_error_paths(monkeypatch):
    _install_fake_registries(monkeypatch)
    FakeStrategyRegistry.strategies = {}
    FakePineRegistry.sources = {}
    runner = CliRunner()

    assert runner.invoke(cli_main.cli, ["strategy", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "list", "--json"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "show", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "status", "missing"]).exit_code != 0

    FakeStrategyRegistry.strategies = {"s1": _strategy("s1")}
    assert runner.invoke(cli_main.cli, ["strategy", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "list", "--json"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "show", "s1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "status", "s1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "pause", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "pause", "s1"]).exit_code == 0
    FakeStrategyRegistry.strategies["s1"].status = "error"
    assert runner.invoke(cli_main.cli, ["strategy", "resume", "s1"]).exit_code != 0
    FakeStrategyRegistry.strategies["s1"].status = "paused"
    assert runner.invoke(cli_main.cli, ["strategy", "resume", "s1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "enable", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "enable", "s1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "disable", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "disable", "s1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["strategy", "remove", "missing"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "remove", "s1"]).exit_code == 0


def test_strategy_cli_create_and_update_edges(monkeypatch):
    _install_fake_registries(monkeypatch)
    FakeStrategyRegistry.strategies = {}
    FakePineRegistry.sources = {}
    runner = CliRunner()

    assert runner.invoke(cli_main.cli, ["strategy", "create", "s2", "--pine", "p", "--symbol", "BTCUSDT", "--timeframe", "1m", "--param", "bad"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "create", "s2", "--pine", "missing", "--symbol", "BTCUSDT", "--timeframe", "1m"]).exit_code != 0
    FakePineRegistry.sources = {"p": SimpleNamespace(id="pine1", active_artifact_id=None)}
    assert runner.invoke(cli_main.cli, ["strategy", "create", "s2", "--pine", "p", "--symbol", "BTCUSDT", "--timeframe", "1m"]).exit_code != 0
    FakePineRegistry.sources = {"p": SimpleNamespace(id="pine1", active_artifact_id="art1")}
    created = runner.invoke(cli_main.cli, ["strategy", "create", "s2", "--pine", "p", "--symbol", "BTCUSDT", "--timeframe", "1m", "--mode", "live", "--param", "x=1"])
    assert created.exit_code == 0, created.output
    assert FakeStrategyRegistry.strategies["s2"].status == "disabled"

    assert runner.invoke(cli_main.cli, ["strategy", "update", "s2"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "update", "s2", "--param", "bad"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "update", "missing", "--param", "x=2"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["strategy", "update", "s2", "--param", "y=2"]).exit_code == 0
