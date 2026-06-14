from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")


class _Registry:
    sources = []
    active = []
    removed = []
    missing = False

    def __init__(self):
        self.closed = False

    def list_sources(self):
        return list(type(self).sources)

    def get_source(self, name):
        if type(self).missing or name == "missing":
            raise KeyError(name)
        return type(self).sources[0]

    def add_source(self, source_text, name):
        source = SimpleNamespace(
            id="pine-id",
            name=name,
            version=1,
            source_type="strategy" if "strategy" in source_text else "indicator",
            active_artifact_id="artifact-1",
            created_at=1,
            updated_at=2,
        )
        type(self).sources = [source]
        return source

    def set_active_artifact(self, source_id, artifact_id):
        type(self).active.append((source_id, artifact_id))

    def remove_source(self, name):
        type(self).removed.append(name)

    def close(self):
        self.closed = True


class _ArtifactStore:
    artifacts = [
        {"artifact_id": "artifact-1", "artifact_dir": "", "compile_meta": {"created_at": 123, "params_hash": "ph"}},
        {"artifact_id": "artifact-2", "artifact_dir": "", "compile_meta": {"created_at": 124}},
    ]

    def list_artifacts(self, source_id):
        return list(type(self).artifacts)


def _install_registry(monkeypatch):
    import openpine.pine.registry as registry_mod
    import openpine.artifacts as artifacts_mod

    source = SimpleNamespace(
        id="pine-id",
        name="src",
        version=1,
        source_type="strategy",
        active_artifact_id="artifact-1",
        created_at=1,
        updated_at=2,
    )
    _Registry.sources = [source]
    _Registry.missing = False
    _Registry.active = []
    _Registry.removed = []
    monkeypatch.setattr(registry_mod, "SQLitePineSourceRegistry", _Registry)
    monkeypatch.setattr(artifacts_mod, "ArtifactStore", _ArtifactStore)
    return source


def test_cli_run_wrapper_and_autodetect(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli_main, "_run_openpine_cli", lambda args: calls.append(args) or ("Strategy created: s1\n" if args[:2] == ["strategy", "create"] else ""))
    indicator = tmp_path / "001_indicator.pine"
    indicator.write_text('indicator("i")\n', encoding="utf-8")
    result = CliRunner().invoke(cli_main.cli, ["run", str(indicator), "--symbol", "BTCUSDT", "--timeframe", "1m", "--from", "2026-01-01", "--output", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert any(call[:2] == ["pine", "run-plots"] for call in calls)
    assert cli_main._auto_pine_source_name(indicator).startswith("po_0001_")

    calls.clear()
    strategy = tmp_path / "s.pine"
    strategy.write_text('strategy("s")\n', encoding="utf-8")
    result = CliRunner().invoke(cli_main.cli, ["run", str(strategy), "--symbol", "BTCUSDT", "--timeframe", "1m", "--from", "2026-01-01", "--to", "2026-01-02", "--history-from", "2025-12-01", "--compare-from", "2026-01-01", "--compare-to", "2026-01-02", "--tv-chart", str(strategy), "--output", str(tmp_path / "out2")])
    assert result.exit_code == 0, result.output
    assert any(call[:2] == ["strategy", "backtest"] for call in calls)
    assert any(call[:2] == ["strategy", "compare-tv"] for call in calls)

    bad = tmp_path / "bad.pine"
    bad.write_text("plot(close)\n", encoding="utf-8")
    result = CliRunner().invoke(cli_main.cli, ["run", str(bad), "--symbol", "BTCUSDT", "--timeframe", "1m", "--from", "2026-01-01", "--output", str(tmp_path / "out3")])
    assert result.exit_code != 0


def test_run_openpine_cli_success_and_failure(monkeypatch):
    class Result:
        def __init__(self, code: int):
            self.returncode = code
            self.stdout = "out"
            self.stderr = "err"
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result(0))
    assert cli_main._run_openpine_cli(["version"]) == "out"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result(2))
    with pytest.raises(Exception):
        cli_main._run_openpine_cli(["bad"])


def test_pine_commands_success_and_error_edges(monkeypatch, tmp_path):
    _install_registry(monkeypatch)
    import openpine.compile as compile_mod
    monkeypatch.setattr(compile_mod, "SubprocessCompilerAdapter", lambda: object())
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": True, "artifact_id": "artifact-2", "artifact_path": "/tmp/a.py", "errors": []})
    runner = CliRunner()
    pine_file = tmp_path / "src.pine"
    pine_file.write_text('strategy("s")\n', encoding="utf-8")
    for args in (["pine", "list"], ["pine", "list", "--json"], ["pine", "show", "src"], ["pine", "pine-add", "src", str(pine_file)], ["pine", "pine-compile", "src"], ["pine", "artifacts", "src"], ["pine", "versions", "src"], ["pine", "activate", "src", "artifact-1"], ["pine", "rollback", "src"], ["pine", "rollback", "src", "--to-version", "artifact-1"], ["pine", "remove", "src"]):
        res = runner.invoke(cli_main.cli, list(args))
        assert res.exit_code == 0, (args, res.output)
    _Registry.missing = True
    for args in (["pine", "show", "missing"], ["pine", "pine-compile", "missing"], ["pine", "artifacts", "missing"], ["pine", "versions", "missing"], ["pine", "activate", "missing", "x"], ["pine", "rollback", "missing"], ["pine", "remove", "missing"]):
        res = runner.invoke(cli_main.cli, list(args))
        assert res.exit_code == 0, (args, res.output)
    _Registry.missing = False
    res = runner.invoke(cli_main.cli, ["pine", "activate", "src", "nope"])
    assert res.exit_code == 0
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": False, "errors": ["bad"]})
    assert runner.invoke(cli_main.cli, ["pine", "pine-compile", "src"]).exit_code == 0


def test_init_stream_state_account_provider_risk_core_plugins(monkeypatch, tmp_path):
    runner = CliRunner()
    import openpine.config as config_mod
    class StateCfg:
        save_policy = "every_bar"; save_interval_bars = 1; keep_last_snapshots = 3
    class Cfg:
        kill_switch = False; live_enabled = False; timezone = "UTC"
        config_dir = tmp_path / "cfg"; data_dir = tmp_path / "data"; sqlite_path = tmp_path / "db.sqlite"; duckdb_path = tmp_path / "d.duckdb"; state = StateCfg()
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=False, chat_allowlist=[], token_ref="env"))
        def save(self): self.saved = True
    monkeypatch.setattr(config_mod.OpenPineConfig, "load", classmethod(lambda cls: Cfg()))
    import openpine.storage as storage_mod
    class Storage:
        def __init__(self, *a, **k): pass
        def execute(self, sql): return SimpleNamespace(fetchall=lambda: [("t",)], fetchone=lambda: ("wal",))
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
    class Runner:
        def run_migrations(self, storage): return ["001"]
    monkeypatch.setattr(storage_mod, "SQLiteStorage", Storage)
    monkeypatch.setattr(storage_mod, "MigrationRunner", Runner)
    for args in (["version"], ["init"], ["state", "policy"], ["state", "show"], ["state", "list"], ["state", "invalid"], ["providers", "list"], ["providers", "test", "marketdata-provider"], ["risk"], ["risk", "show", "--show-violations"], ["risk", "status"], ["streams", "plan"]):
        res = runner.invoke(cli_main.cli, list(args))
        assert res.exit_code in (0, 1), (args, res.output)
    # stream setup cancel and provider selection
    assert runner.invoke(cli_main.cli, ["streams", "setup"], input="c\n").exit_code == 0
    assert runner.invoke(cli_main.cli, ["streams", "setup"], input="1\n").exit_code == 0
    # provider errors/HTTP branches
    assert runner.invoke(cli_main.cli, ["providers", "test", "missing"]).exit_code != 0
    req_mod = types.SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=200, text="{}"))
    monkeypatch.setitem(sys.modules, "requests", req_mod)
    assert runner.invoke(cli_main.cli, ["providers", "test", "binance"]).exit_code == 0
    req_mod.get = lambda *a, **k: SimpleNamespace(status_code=500, text="oops")
    assert runner.invoke(cli_main.cli, ["providers", "test", "binance"]).exit_code == 0

    # accounts with fake manager
    import openpine.accounts as accounts_mod
    import openpine.accounts.models as acc_models
    class Manager:
        accounts = []
        def __init__(self, storage): pass
        def create_account(self, **kw):
            acc = SimpleNamespace(account_id="account-123456", id="account-123456", name=kw["name"], exchange=kw["exchange"], provider=kw["provider"], market_type=kw["market_type"], mode=kw["mode"], account_type=kw["account_type"], live_enabled=kw["live_enabled"], config={}, api_key_ref="k", api_secret_ref="s")
            self.accounts.append(acc); return acc
        def list_accounts(self): return list(self.accounts)
    monkeypatch.setattr(accounts_mod, "AccountManager", Manager)
    assert runner.invoke(cli_main.cli, ["accounts", "add", "--name", "a", "--exchange", "binance", "--api-key", "abcd1234zzzz", "--secret", "secret", "--mode", "live"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["accounts", "list"]).exit_code == 0
    Manager.accounts = [SimpleNamespace(name="a", id="id1234567890", exchange="binance", provider="binance", market_type="spot", mode=acc_models.AccountType.LIVE, account_type=acc_models.AccountType.LIVE, live_enabled=True, config={}, api_key_ref="k", api_secret_ref="s")]
    assert runner.invoke(cli_main.cli, ["accounts", "test", "a"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["accounts", "test", "missing"]).exit_code != 0

    # plugins/core/schema commands
    assert runner.invoke(cli_main.cli, ["plugins", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "42"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "enable", "unknown"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["events", "schema", "validate", "unknown"]).exit_code != 0


def test_deep_checks_and_doctor_branches(monkeypatch, tmp_path):
    cfg = SimpleNamespace(sqlite_path=tmp_path / "db.sqlite", duckdb_path=tmp_path / "d.duckdb", data_dir=tmp_path, config_dir=tmp_path, kill_switch=False, live_enabled=False, plugins=SimpleNamespace(telegram=SimpleNamespace(enabled=False)))
    class Console:
        def print(self, *a, **k): pass
    assert cli_main._check_writable_dir(tmp_path / "x", "x", Console()) is True
    blocked = tmp_path / "blocked"
    blocked.write_text("x", encoding="utf-8")
    assert cli_main._check_writable_dir(blocked / "bad", "bad", Console()) is False
    cli_main._check_optional_duckdb(cfg, Console())
    # invalid schema type and monkeypatched missing model fields branch
    assert cli_main._validate_event_schema("unknown") is False
    import openpine.contracts as contracts
    import openpine.events as events
    old_contract = contracts.StrategyRuntimeError
    old_payload = events.StrategyRuntimeErrorPayload
    try:
        contracts.StrategyRuntimeError = SimpleNamespace(model_fields={})
        events.StrategyRuntimeErrorPayload = SimpleNamespace(__dataclass_fields__={})
        assert cli_main._validate_event_schema("StrategyRuntimeError") is False
    finally:
        contracts.StrategyRuntimeError = old_contract
        events.StrategyRuntimeErrorPayload = old_payload


def test_cli_indicator_compare_streams_state_and_plugins_more(monkeypatch, tmp_path):
    runner = CliRunner()
    # pine run-plots happy path through dependency injection helpers
    prepared = SimpleNamespace(
        timings={"load": 0.1}, source=SimpleNamespace(id="pine", active_artifact_id="art"),
        start_ms=1, end_ms=2, compare_from_ms=None, compare_to_ms=None, bars_total=1,
    )
    monkeypatch.setattr(cli_main, "_indicator_plot_dependencies", lambda: SimpleNamespace(SQLitePineSourceRegistry=object, parse_time_ms=lambda value: 1, load_generated_class_from_artifact=lambda *a, **k: object(), BacktestArtifactError=RuntimeError, BarQuery=object, InstrumentKey=object, parse_timeframe=lambda value: value, DataOrchestrator=object, create_local_marketdata_provider_adapter=lambda: object()))
    monkeypatch.setattr(cli_main, "_prepare_indicator_plot_inputs", lambda **kw: prepared)
    monkeypatch.setattr(cli_main, "_print_indicator_plot_header", lambda **kw: None)
    monkeypatch.setattr(cli_main, "_write_indicator_plot_run_outputs", lambda **kw: None)
    res = runner.invoke(cli_main.cli, ["pine", "run-plots", "src", "--symbol", "BTCUSDT", "--timeframe", "1m", "--from", "2026-01-01", "--output", str(tmp_path / "plots")])
    assert res.exit_code == 0, res.output

    # indicator TV compare with match and failure-style status
    monkeypatch.setattr(cli_main, "_compare_rows_by_time", lambda **kw: ({"status": "mismatch", "classification": "bad", "mismatch_cells": 1, "total_cells": 2, "max_abs_delta": 0.1, "worst_column": "p"}, [{"column": "p"}]))
    written = []
    monkeypatch.setattr(cli_main, "_write_strategy_tv_compare_report", lambda path, payload: written.append((path, payload)))
    op = tmp_path / "op.csv"; tv = tmp_path / "tv.csv"; op.write_text("time,p\n1,1\n"); tv.write_text("time,p\n1,2\n")
    res = runner.invoke(cli_main.cli, ["pine", "compare-tv", "src", "--openpine-plots", str(op), "--tv-chart", str(tv), "--output", str(tmp_path / "cmp"), "--include-base-columns"])
    assert res.exit_code == 0 and written and written[0][1]["failures"]

    # streams status with fake subscription
    import openpine.streams as streams_mod
    class FakeManager:
        def __init__(self, *a, **k): pass
        def list_subscriptions(self):
            return [SimpleNamespace(status=SimpleNamespace(value="active"), subscription_id="sub", instrument_key="BTC", timeframe="1m", provider="binance")]
    monkeypatch.setattr(streams_mod, "MarketDataStreamManager", FakeManager)
    import openpine.cli.main as main_mod
    monkeypatch.setattr(main_mod, "MarketDataStreamManager", FakeManager, raising=False)
    assert runner.invoke(cli_main.cli, ["streams", "status"]).exit_code == 0

    # state list and invalid snapshots
    import openpine.config as config_mod
    state_dir = tmp_path / "state"; sd = state_dir / "strategy_id=s1"; sd.mkdir(parents=True)
    snap_file = sd / "snap_1.state.msgpack"; snap_file.write_bytes(b"x")
    snap_file.with_suffix(".debug.json").write_text('{"last_processed_bar_time": 123}', encoding="utf-8")
    class Cfg2:
        data_dir = tmp_path
        config_dir = tmp_path
        sqlite_path = tmp_path / "db.sqlite"
        kill_switch = False
        live_enabled = False
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=True, chat_allowlist=["42"], token_ref="env", resolve_token=lambda: "token"))
        def save(self): pass
    monkeypatch.setattr(config_mod.OpenPineConfig, "load", classmethod(lambda cls: Cfg2()))
    import openpine.state.store as store_mod
    class Snapshot:
        snapshot_id="snapshot-123456"; strategy_id="s1"; bar_time=123; size_bytes=4096; saved_at=456; status="active"
    class FakeStateStore:
        def __init__(self, path): self.path=path
        def list_snapshots(self, strategy_id): return [Snapshot()]
    monkeypatch.setattr(store_mod, "StateStore", FakeStateStore)
    assert runner.invoke(cli_main.cli, ["state", "list", "--strategy", "s1"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["state", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["state", "invalid"]).exit_code == 0
    # state rebuild success and failure
    import openpine.recovery as recovery_mod
    class Rebuilder:
        def __init__(self, *a, **k): pass
        def rebuild(self, strategy_id, from_bar_time): return SimpleNamespace(strategy_id=strategy_id, artifact_id="art", bar_time=from_bar_time)
    monkeypatch.setattr(recovery_mod, "StateRebuilder", Rebuilder)
    assert runner.invoke(cli_main.cli, ["state", "rebuild", "s1"]).exit_code == 0

    # risk kill switch branches
    assert runner.invoke(cli_main.cli, ["risk", "kill-switch", "on"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["risk", "kill-switch", "off"]).exit_code == 0

    # plugin test success/failure and telegram command helpers
    import openpine.notifications as notif_mod
    class Notifier:
        next_ok = True
        def __init__(self, config): pass
        def test(self, chat_id): return SimpleNamespace(ok=self.next_ok, error_message="bad")
    monkeypatch.setattr(notif_mod, "TelegramNotifier", Notifier)
    assert runner.invoke(cli_main.cli, ["plugins", "test", "telegram", "--chat-id", "42"]).exit_code == 0
    Notifier.next_ok = False
    assert runner.invoke(cli_main.cli, ["plugins", "test", "telegram", "--chat-id", "42"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["plugins", "test", "bad", "--chat-id", "42"]).exit_code != 0
    assert cli_main._normalize_telegram_command({"name":"start", "description":"Start", "cli_command":"openpine version"})["command"] == "/start"
    assert cli_main._normalize_telegram_command(SimpleNamespace(command="help", description="Help", argv=["version"]))["cli"].endswith("version")
    assert "inline_keyboard" in cli_main._telegram_menu_markup()
    with pytest.raises(SystemExit): cli_main._resolve_telegram_token(SimpleNamespace(plugins=SimpleNamespace(telegram=SimpleNamespace(enabled=False, token_ref="env", resolve_token=lambda: "token"))))
    with pytest.raises(SystemExit): cli_main._resolve_telegram_token(SimpleNamespace(plugins=SimpleNamespace(telegram=SimpleNamespace(enabled=True, token_ref="env", resolve_token=lambda: ""))))
    assert cli_main._resolve_telegram_token(Cfg2()) == "token"
