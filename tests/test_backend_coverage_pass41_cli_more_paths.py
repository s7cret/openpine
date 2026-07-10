from __future__ import annotations

import asyncio
import builtins
import importlib
import math
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

cli_main = importlib.import_module("openpine.cli.main")
cli_data = importlib.import_module("openpine.cli.data")
cli_compare = importlib.import_module("openpine.cli.compare")
cli_ops = importlib.import_module("openpine.cli.ops")
cli_reports = importlib.import_module("openpine.cli.reports")


class _SinkConsole:
    def __init__(self) -> None:
        self.lines: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def print(self, *args, **kwargs) -> None:
        self.lines.append((args, kwargs))


def _cfg(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    telegram = SimpleNamespace(
        enabled=True,
        chat_allowlist=["42"],
        token_ref="env",
        resolve_token=lambda: "token",
    )
    cfg = SimpleNamespace(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
        duckdb_path=tmp_path / "openpine.duckdb",
        kill_switch=False,
        live_enabled=False,
        state=None,
        plugins=SimpleNamespace(telegram=telegram),
        save=lambda: None,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _patch_config(monkeypatch: pytest.MonkeyPatch, cfg: SimpleNamespace) -> None:
    import openpine.config as config_mod

    monkeypatch.setattr(config_mod.OpenPineConfig, "load", classmethod(lambda cls: cfg))


def _raising(exc: Exception):
    def _raise(*args, **kwargs):
        raise exc

    return _raise


def test_main_run_wrapper_empty_output_and_strategy_run_without_capture(monkeypatch, tmp_path):
    class Result:
        stdout = ""
        stderr = ""
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    assert cli_main._run_openpine_cli(["version"]) == ""

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["strategy", "create"]:
            return "Strategy created: strategy-no-capture\n"
        return ""

    monkeypatch.setattr(cli_main, "_run_openpine_cli", fake_run)
    source = tmp_path / "simple_strategy.pine"
    source.write_text("//@version=6\nstrategy('s')\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli_main.cli,
        [
            "run",
            str(source),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1m",
            "--from",
            "2026-01-01",
            "--output",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    backtest_args = next(call for call in calls if call[:2] == ["strategy", "backtest"])
    assert "--capture-plots" not in backtest_args


def test_main_deep_checks_job_queue_and_doctor_import_failure(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)
    sink = _SinkConsole()

    import openpine.jobs as jobs_mod

    class EmptyScheduler:
        def recover_stale_locks(self):
            return 0

        def list_jobs(self, status=None):
            return []

    monkeypatch.setattr(jobs_mod, "JobScheduler", EmptyScheduler)
    cli_main._check_job_queue_health(sink)

    import openpine.accounts as accounts_mod
    import openpine.data.orchestrator as orchestrator_mod
    import openpine.integrations as integrations_mod
    import openpine.notifications as notifications_mod
    import openpine.storage as storage_mod
    import openpine.workers as workers_mod

    monkeypatch.setattr(
        integrations_mod,
        "check_core_libraries",
        lambda: [SimpleNamespace(name="pine2ast", importable=True, version="1", error=None)],
    )
    monkeypatch.setattr(cli_main, "_check_sqlite_reachable", lambda config, console: True)
    monkeypatch.setattr(cli_main, "_check_sqlite_wal_mode", lambda config, console: None)

    def fake_writable(path: Path, label: str, console) -> bool:
        return label not in {"Parquet data dir", "State dir"}

    monkeypatch.setattr(cli_main, "_check_writable_dir", fake_writable)
    monkeypatch.setattr(
        orchestrator_mod,
        "DataOrchestrator",
        _raising(RuntimeError("orchestrator boom")),
    )

    class Storage:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(storage_mod, "SQLiteStorage", Storage)
    monkeypatch.setattr(
        accounts_mod,
        "AccountManager",
        _raising(RuntimeError("account boom")),
    )
    monkeypatch.setattr(
        workers_mod,
        "AggregationWorkerPool",
        _raising(RuntimeError("worker boom")),
    )
    monkeypatch.setattr(workers_mod, "FeatureWorkerPool", object)
    monkeypatch.setattr(
        notifications_mod,
        "PluginManager",
        _raising(RuntimeError("plugin boom")),
    )
    monkeypatch.setattr(notifications_mod, "TelegramCommandPlugin", lambda config: object())

    assert cli_main._run_deep_checks(cfg, sink, True) is False

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "structlog":
            raise ImportError("forced missing structlog")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = CliRunner().invoke(cli_main.cli, ["doctor"])
    assert result.exit_code == 1
    assert "structlog" in result.output


def test_main_doctor_strict_failed_strict_check(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)

    import openpine.integrations as integrations_mod
    import openpine.jobs as jobs_mod
    import openpine.optimizer as optimizer_mod
    import openpine.state as state_mod
    import openpine.workers as workers_mod

    monkeypatch.setattr(integrations_mod, "check_core_libraries", lambda: [])
    monkeypatch.setattr(cli_main, "_validate_event_schema", lambda event_type: True)
    monkeypatch.setattr(
        jobs_mod,
        "Job",
        SimpleNamespace(__dataclass_fields__={"serialization_key": object()}),
    )
    monkeypatch.setattr(state_mod, "SavePolicy", SimpleNamespace(EVERY_BAR="every_bar"))

    class SnapshotPolicy:
        save_policy = "every_bar"
        save_interval_bars = 1

    monkeypatch.setattr(state_mod, "SnapshotPolicy", SnapshotPolicy)

    class AggPool:
        JOB_TYPES = {"same"}

    class FeaturePool:
        JOB_TYPES = {"same"}

    monkeypatch.setattr(workers_mod, "AggregationWorkerPool", AggPool)
    monkeypatch.setattr(workers_mod, "FeatureWorkerPool", FeaturePool)

    class OptimizerService:
        def validate_config(self, strategy_id, trials):
            return SimpleNamespace(status="valid")

    monkeypatch.setattr(optimizer_mod, "OptimizerService", OptimizerService)

    result = CliRunner().invoke(cli_main.cli, ["doctor", "--strict"])
    assert result.exit_code == 1
    assert "Some checks failed" in result.output


def test_main_pine_init_stream_state_and_provider_edges(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)
    runner = CliRunner()

    import openpine.artifacts as artifacts_mod
    import openpine.data.provider_adapter as provider_adapter_mod
    import openpine.events as events_mod
    import openpine.pine.registry as pine_registry_mod
    import openpine.storage as storage_mod
    import openpine.streams as streams_mod

    class PineRegistry:
        def get_source(self, name):
            return SimpleNamespace(id="pine-id", name=name, active_artifact_id=None)

        def close(self):
            pass

    class EmptyArtifactStore:
        def list_artifacts(self, source_id):
            return []

    monkeypatch.setattr(pine_registry_mod, "SQLitePineSourceRegistry", PineRegistry)
    monkeypatch.setattr(artifacts_mod, "ArtifactStore", EmptyArtifactStore)
    result = runner.invoke(cli_main.cli, ["pine", "versions", "src"])
    assert result.exit_code == 0, result.output
    assert "no artifacts" in result.output

    class Storage:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    class MigrationRunner:
        def run_migrations(self, storage):
            return []

    monkeypatch.setattr(storage_mod, "SQLiteStorage", Storage)
    monkeypatch.setattr(storage_mod, "MigrationRunner", MigrationRunner)
    result = runner.invoke(cli_main.cli, ["init"])
    assert result.exit_code == 0, result.output
    assert "No pending migrations" in result.output

    class EventBus:
        def __init__(self, storage):
            self.storage = storage

    class DataOrchestrator:
        pass

    class EmptyStreamManager:
        def __init__(self, *args, **kwargs):
            pass

        def list_subscriptions(self):
            return []

    import openpine.data.orchestrator as orchestrator_mod

    monkeypatch.setattr(events_mod, "EventBus", EventBus)
    monkeypatch.setattr(orchestrator_mod, "DataOrchestrator", DataOrchestrator)
    monkeypatch.setattr(streams_mod, "MarketDataStreamManager", EmptyStreamManager)
    result = runner.invoke(cli_main.cli, ["streams", "status"])
    assert result.exit_code == 0, result.output
    assert "No active subscriptions" in result.output

    state_dir = cfg.data_dir / "state" / "strategy_id=s-bad-json"
    state_dir.mkdir(parents=True)
    snap = state_dir / "snap_1.state.msgpack"
    snap.write_bytes(b"state")
    snap.with_suffix(".debug.json").write_text("{not-json", encoding="utf-8")
    result = runner.invoke(cli_main.cli, ["state", "invalid"])
    assert result.exit_code == 0, result.output
    assert "bar_time=0" in result.output

    monkeypatch.setattr(
        provider_adapter_mod,
        "create_local_marketdata_provider_adapter",
        _raising(RuntimeError("adapter boom")),
    )
    result = runner.invoke(cli_main.cli, ["providers", "list"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli_main.cli, ["providers", "test", "marketdata-provider"])
    assert result.exit_code == 1

    monkeypatch.setitem(
        cli_main._KNOWN_PROVIDERS,
        "no-rest",
        {"name": "No REST", "rest": "N/A (local)", "ws": "-"},
    )
    result = runner.invoke(cli_main.cli, ["providers", "test", "no-rest"])
    assert result.exit_code == 0, result.output
    assert "No REST endpoint" in result.output


def test_main_plugins_telegram_schema_and_strategy_missing_edges(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)
    runner = CliRunner()

    import openpine.notifications as notifications_mod

    monkeypatch.delattr(notifications_mod, "PluginManager", raising=False)
    result = runner.invoke(cli_main.cli, ["plugins", "list"])
    assert result.exit_code == 0, result.output
    assert "telegram" in result.output

    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "poll", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "network: skipped" in result.output

    calls: list[tuple[str, dict[str, object] | None]] = []

    def fake_api(token: str, method: str, payload: dict[str, object] | None = None):
        calls.append((method, payload))
        if method == "getUpdates":
            return {"result": [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "/start"}}]}
        return {"ok": True, "method": method}

    monkeypatch.setattr(cli_main, "_resolve_telegram_token", lambda config: "token")
    monkeypatch.setattr(cli_main, "_telegram_api_request", fake_api)
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "poll", "--once"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "webhook-info"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "42"])
    assert result.exit_code == 0, result.output
    assert [method for method, _payload in calls] == ["getUpdates", "getWebhookInfo", "sendMessage"]

    monkeypatch.setattr(cli_main, "_validate_event_schema", lambda event_type: False)
    result = runner.invoke(cli_main.cli, ["events", "schema", "StrategyRuntimeError"])
    assert result.exit_code == 1

    import openpine.registry as registry_mod

    class MissingRegistry:
        def get_strategy(self, strategy_id):
            raise KeyError(strategy_id)

        def close(self):
            pass

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", MissingRegistry)
    assert runner.invoke(cli_main.cli, ["strategy", "resume", "missing"]).exit_code == 1
    assert runner.invoke(cli_main.cli, ["strategy", "paper", "missing", "start"]).exit_code == 1
    assert runner.invoke(cli_main.cli, ["strategy", "live", "missing", "enable"]).exit_code == 1


def test_main_strategy_replay_success_readiness_and_engine_failure(monkeypatch):
    runner = CliRunner()

    import openpine.registry as registry_mod
    import openpine.runtime.engine as engine_mod

    class Registry:
        updates: list[tuple[str, str]] = []
        strategy = SimpleNamespace(
            strategy_id="s1",
            name="Replay Strategy",
            status="paused",
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            active_artifact_id="artifact-1",
            params_json="{}",
        )

        def get_strategy(self, strategy_id):
            return type(self).strategy

        def update_status(self, strategy_id, status):
            type(self).updates.append((strategy_id, status))

        def close(self):
            type(self).updates.append(("closed", "closed"))

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", Registry)
    monkeypatch.setattr(cli_main, "_get_strategy_or_exit", lambda **kwargs: Registry.strategy)
    monkeypatch.setattr(cli_main, "_print_strategy_command_header", lambda **kwargs: None)
    monkeypatch.setattr(cli_main, "_strategy_backtest_readiness_error", lambda strategy: None)
    monkeypatch.setattr(
        cli_main,
        "_prepare_strategy_replay_inputs",
        lambda **kwargs: SimpleNamespace(
            strategy_class=object,
            bars=[object(), object()],
            config=SimpleNamespace(),
            params={"length": 3},
        ),
    )

    class Adapter:
        fail = False

        def run(self, strategy_class, bars, config, params):
            if type(self).fail:
                raise RuntimeError("engine boom")
            return SimpleNamespace(status="ok", bars_processed=len(bars), uses_backtest_engine=True)

    monkeypatch.setattr(engine_mod, "BacktestEngineAdapter", Adapter)

    result = runner.invoke(cli_main.cli, ["strategy", "replay", "s1", "--from", "2026-01-01"])
    assert result.exit_code == 0, result.output
    assert "Replay completed" in result.output
    assert ("s1", "running") in Registry.updates
    assert ("s1", "paused") in Registry.updates

    monkeypatch.setattr(cli_main, "_strategy_backtest_readiness_error", lambda strategy: "not ready")
    result = runner.invoke(cli_main.cli, ["strategy", "replay", "s1"])
    assert result.exit_code == 1

    monkeypatch.setattr(cli_main, "_strategy_backtest_readiness_error", lambda strategy: None)
    Adapter.fail = True
    result = runner.invoke(cli_main.cli, ["strategy", "replay", "s1"])
    assert result.exit_code == 1
    assert "Replay failed" in result.output


def test_main_strategy_artifact_and_compare_missing_paths(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)
    runner = CliRunner()

    import openpine.registry as registry_mod
    import openpine.storage as storage_mod

    class Registry:
        missing = False
        strategy = SimpleNamespace(strategy_id="s1", name="Strategy One")

        def get_strategy(self, strategy_id):
            if type(self).missing:
                raise KeyError(strategy_id)
            return type(self).strategy

        def close(self):
            pass

    monkeypatch.setattr(registry_mod, "SQLiteStrategyRegistry", Registry)

    tv_chart = tmp_path / "tv_chart.csv"
    tv_chart.write_text("time,value\n1,1\n", encoding="utf-8")
    Registry.missing = True
    result = runner.invoke(
        cli_main.cli,
        [
            "strategy",
            "compare-tv",
            "missing",
            "--tv-chart",
            str(tv_chart),
            "--output",
            str(tmp_path / "cmp-missing"),
        ],
    )
    assert result.exit_code == 1

    Registry.missing = False

    class StoreNoRun:
        def __init__(self, *args, **kwargs):
            pass

        def get_latest_run(self, strategy_id):
            return None

        def get_run(self, run_id):
            return None

        def list_artifacts(self, run_id):
            return []

        def list_trades(self, run_id):
            return []

        def close(self):
            pass

    monkeypatch.setattr(storage_mod, "BacktestResultStore", StoreNoRun)
    result = runner.invoke(
        cli_main.cli,
        [
            "strategy",
            "compare-tv",
            "s1",
            "--tv-chart",
            str(tv_chart),
            "--output",
            str(tmp_path / "cmp-norun"),
        ],
    )
    assert result.exit_code == 1
    assert "No backtest runs" in result.output

    class StoreNoArtifacts(StoreNoRun):
        def get_latest_run(self, strategy_id):
            return SimpleNamespace(run_id="run-1")

        def get_run(self, run_id):
            return SimpleNamespace(run_id=run_id)

    monkeypatch.setattr(storage_mod, "BacktestResultStore", StoreNoArtifacts)
    result = runner.invoke(cli_main.cli, ["strategy", "equity", "s1"])
    assert result.exit_code == 1
    assert "No equity curve artifact" in result.output
    result = runner.invoke(cli_main.cli, ["strategy", "plots", "s1"])
    assert result.exit_code == 1
    assert "No plot outputs artifact" in result.output

    exported, rows = cli_main._write_strategy_export_files(
        strategy_id="s1",
        run=SimpleNamespace(run_id="run-1", metrics=SimpleNamespace()),
        artifacts=[],
        trades=[],
        output_path=tmp_path / "export-empty-plots",
        compare_from=None,
        compare_to=None,
        no_plots=False,
        no_trades=True,
        no_metrics=True,
    )
    assert exported == {}
    assert rows["plots"] == 0


def test_main_daemon_signal_telegram_start_and_stop_error_paths(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    cfg.plugins.telegram.enabled = True
    _patch_config(monkeypatch, cfg)

    import openpine.daemon.refresh_service as refresh_service_mod
    import openpine.daemon.telegram_service as telegram_service_mod

    class RefreshService:
        name = "refresh"

        async def start(self):
            return None

        async def stop(self, timeout=5.0):
            raise RuntimeError("refresh stop boom")

    class TelegramService:
        name = "telegram"

        async def start(self):
            raise RuntimeError("telegram start boom")

        async def stop(self, timeout=5.0):
            raise RuntimeError("telegram stop boom")

    monkeypatch.setattr(refresh_service_mod, "MarketDataRefreshService", RefreshService)
    monkeypatch.setattr(telegram_service_mod, "TelegramDaemonService", TelegramService)

    class FakeLoop:
        def __init__(self) -> None:
            self.removed: list[signal.Signals] = []
            self.stopped = False

        def add_signal_handler(self, sig, callback, *args):
            callback(*args)

        def remove_signal_handler(self, sig):
            self.removed.append(sig)

        def stop(self):
            self.stopped = True

    fake_loop = FakeLoop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fake_loop)

    async def fake_sleep(delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cli_main.sys, "platform", "linux")

    result = CliRunner().invoke(cli_main.cli, ["daemon", "run"])
    assert result.exit_code == 0, result.output
    assert "could not start Telegram service" in result.output
    assert "Received signal" in result.output
    assert "Error stopping" in result.output
    assert fake_loop.removed
    assert fake_loop.stopped is True


def test_data_sync_backfill_parse_status_gaps_repair_and_provider_edges(monkeypatch, tmp_path):
    start_ms, end_ms, error = cli_data._parse_data_backfill_window(
        from_date="2026-01-01",
        to_date="bad-date",
        now_ms=123,
    )
    assert (start_ms, end_ms) == (None, None)
    assert "Invalid --to" in error

    import openpine.data.orchestrator as orchestrator_mod
    import openpine.data.provider_adapter as provider_adapter_mod

    monkeypatch.setattr(provider_adapter_mod, "create_local_marketdata_provider_adapter", lambda: object())

    handler_holder: dict[str, object] = {}

    def fake_signal(sig, handler):
        handler_holder["handler"] = handler
        return lambda *_args: None

    def fake_setitimer(kind, seconds):
        if seconds:
            handler_holder["handler"](None, None)

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(signal, "setitimer", fake_setitimer)
    assert (
        cli_data._run_sync_marketdata_backfill(
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market="spot",
            start_ms=0,
            end_ms=60_000,
            timeout=1,
            console=_SinkConsole(),
        )
        is False
    )

    class FakeOrchestrator:
        response: object = SimpleNamespace(bars=[])

        def __init__(self, provider=None):
            self._provider = provider

        def load_bars(self, query):
            if isinstance(type(self).response, BaseException):
                raise type(self).response
            return type(self).response

        def detect_gaps(self, query):
            return []

    monkeypatch.setattr(orchestrator_mod, "DataOrchestrator", FakeOrchestrator)
    FakeOrchestrator.response = orchestrator_mod.DataCoverageError("coverage boom")
    assert (
        cli_data._run_sync_marketdata_backfill(
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market="spot",
            start_ms=0,
            end_ms=60_000,
            timeout=0,
            console=_SinkConsole(),
        )
        is False
    )
    FakeOrchestrator.response = RuntimeError("generic boom")
    assert (
        cli_data._run_sync_marketdata_backfill(
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market="spot",
            start_ms=0,
            end_ms=60_000,
            timeout=0,
            console=_SinkConsole(),
        )
        is False
    )
    FakeOrchestrator.response = SimpleNamespace(bars=[])
    assert (
        cli_data._run_sync_marketdata_backfill(
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market="spot",
            start_ms=0,
            end_ms=60_000,
            timeout=0,
            console=_SinkConsole(),
        )
        is False
    )

    cfg = _cfg(tmp_path)
    (cfg.data_dir / "candles").mkdir(parents=True)
    _patch_config(monkeypatch, cfg)

    import openpine.registry as strategy_registry_mod
    import openpine.storage as storage_mod

    class StatusStorage:
        mode = "rows"

        def __init__(self, *args, **kwargs):
            pass

        def execute(self, sql):
            if type(self).mode == "raise":
                raise RuntimeError("status query boom")
            rows = []
            if type(self).mode == "rows":
                rows = [("binance", "BTCUSDT", "1m", "local", "ok", 1_700_000_000_000)]
            return SimpleNamespace(fetchall=lambda: rows)

        def close(self):
            pass

    monkeypatch.setattr(storage_mod, "SQLiteStorage", StatusStorage)

    original_rglob = Path.rglob

    def fake_rglob(self: Path, pattern: str):
        if self == cfg.data_dir / "candles":
            raise RuntimeError("parquet scan boom")
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", fake_rglob)
    runner = CliRunner()
    result = runner.invoke(cli_data.data, ["status"])
    assert result.exit_code == 0, result.output
    assert "Data Requirements" in result.output
    StatusStorage.mode = "empty"
    result = runner.invoke(cli_data.data, ["status"])
    assert result.exit_code == 0, result.output
    assert "no data requirements" in result.output
    StatusStorage.mode = "raise"
    result = runner.invoke(cli_data.data, ["status"])
    assert result.exit_code == 0, result.output
    assert "Could not query data_requirements" in result.output

    result = runner.invoke(cli_data.data, ["gaps", "BTCUSDT", "1m"])
    assert result.exit_code == 0, result.output
    assert "No gaps found" in result.output

    class Scheduler:
        def enqueue(self, job):
            return SimpleNamespace(id="repair-job-123456")

    class StrategyRegistry:
        def list_strategies(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(cli_data, "_cli_scheduler", Scheduler())
    monkeypatch.setattr(strategy_registry_mod, "SQLiteStrategyRegistry", StrategyRegistry)
    result = runner.invoke(
        cli_data.data,
        ["repair", "BTCUSDT", "1m", "--from", "0", "--to", "60000"],
    )
    assert result.exit_code == 0, result.output
    assert "No matching strategies" in result.output

    result = runner.invoke(
        cli_data.data,
        [
            "parallel-backfill",
            "BTCUSDT",
            "1m",
            "--from",
            "2026-01-01",
            "--to",
            "not-a-date",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Invalid --to date format" in result.output

    monkeypatch.setattr(
        provider_adapter_mod,
        "create_local_marketdata_provider_adapter",
        _raising(RuntimeError("provider list boom")),
    )
    result = runner.invoke(cli_data.data, ["providers"])
    assert result.exit_code == 0, result.output
    assert "Available Data Providers" in result.output


def test_data_inspect_and_doctor_error_and_classification_paths(monkeypatch):
    import openpine.data.orchestrator as orchestrator_mod

    class FakeOrchestrator:
        response: object = SimpleNamespace(
            bars=[],
            coverage=SimpleNamespace(status="valid", missing_intervals=[], duplicate_timestamps=[]),
        )

        def load_bars(self, query):
            if isinstance(type(self).response, BaseException):
                raise type(self).response
            return type(self).response

    monkeypatch.setattr(orchestrator_mod, "DataOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    FakeOrchestrator.response = orchestrator_mod.DataCoverageError("inspect boom")
    result = runner.invoke(
        cli_data.data,
        ["inspect", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"],
    )
    assert result.exit_code == 0, result.output
    assert "Data read failed" in result.output

    FakeOrchestrator.response = SimpleNamespace(
        bars=[],
        coverage=SimpleNamespace(status="valid", missing_intervals=[], duplicate_timestamps=[]),
    )
    result = runner.invoke(
        cli_data.data,
        ["inspect", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"],
    )
    assert result.exit_code == 0, result.output
    assert "no bars in range" in result.output

    FakeOrchestrator.response = orchestrator_mod.DataCoverageError("doctor boom")
    result = runner.invoke(
        cli_data.data,
        ["doctor", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"],
    )
    assert result.exit_code == 0, result.output
    assert "Data doctor failed" in result.output

    coverage = SimpleNamespace(status="valid", missing_intervals=[], duplicate_timestamps=[])
    FakeOrchestrator.response = SimpleNamespace(bars=[], coverage=coverage)
    result = runner.invoke(
        cli_data.data,
        ["doctor", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"],
    )
    assert result.exit_code == 0, result.output
    assert "NO_DATA" in result.output

    coverage = SimpleNamespace(status="valid", missing_intervals=[], duplicate_timestamps=[123])
    FakeOrchestrator.response = SimpleNamespace(bars=[object()], coverage=coverage)
    result = runner.invoke(
        cli_data.data,
        ["doctor", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"],
    )
    assert result.exit_code == 0, result.output
    assert "DUPLICATE_TIMESTAMPS" in result.output
    assert "duplicate timestamps" in result.output

    coverage = SimpleNamespace(status="stale", missing_intervals=[], duplicate_timestamps=[])
    FakeOrchestrator.response = SimpleNamespace(bars=[object()], coverage=coverage)
    result = runner.invoke(
        cli_data.data,
        ["doctor", "BTCUSDT", "1m", "--from", "2026-01-01", "--to", "2026-01-02"],
    )
    assert result.exit_code == 0, result.output
    assert "STALE" in result.output


def test_compare_scalar_normalization_row_comparison_and_run_export_edges(monkeypatch, tmp_path):
    assert math.isnan(cli_compare._compare_csv_float(None))
    assert cli_compare._compare_csv_time_ms(None) is None
    assert cli_compare._compare_csv_time_ms("   ") is None

    malformed = tmp_path / "malformed_trades.csv"
    malformed.write_text("Trade #,Type,Date/Time,Price\n,Entry Long,1000,10\n9,Comment,1000,11\n", encoding="utf-8")
    out = cli_compare._write_normalized_tv_trades(
        tv_path=malformed,
        output_path=tmp_path / "normalized_malformed.csv",
        compare_from_ms=None,
        compare_to_ms=None,
    )
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("trade_id")

    weird = tmp_path / "weird_trades.csv"
    weird.write_text("Trade #,Type,Date/Time,Price\n1,Entry Long,1000,10\n", encoding="utf-8")
    monkeypatch.setattr(cli_compare, "_trade_action_and_direction", lambda value: ("hold", None))
    out = cli_compare._write_normalized_tv_trades(
        tv_path=weird,
        output_path=tmp_path / "normalized_weird.csv",
        compare_from_ms=None,
        compare_to_ms=None,
    )
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    monkeypatch.setattr(
        cli_compare,
        "_trade_action_and_direction",
        lambda value: ("entry", "long") if "Entry" in str(value) else ("exit", "long"),
    )

    windowed = tmp_path / "windowed_trades.csv"
    windowed.write_text(
        "Trade #,Type,Date/Time,Price,Qty,Net Profit,Run-up,Drawdown,Signal\n"
        "1,Entry Long,1000,10,1,,,,buy\n"
        "1,Exit Long,1001,11,1,1,2,0,sell\n",
        encoding="utf-8",
    )
    out = cli_compare._write_normalized_tv_trades(
        tv_path=windowed,
        output_path=tmp_path / "normalized_after_from.csv",
        compare_from_ms=2_000_000,
        compare_to_ms=None,
    )
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    out = cli_compare._write_normalized_tv_trades(
        tv_path=windowed,
        output_path=tmp_path / "normalized_before_to.csv",
        compare_from_ms=None,
        compare_to_ms=1_001_000,
    )
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1

    tv_nan = tmp_path / "tv_nan.csv"
    op_nan = tmp_path / "op_nan.csv"
    tv_nan.write_text("time,a\n1000,\n", encoding="utf-8")
    op_nan.write_text("bar_time,a\n1000,1\n", encoding="utf-8")
    summary, top = cli_compare._compare_rows_by_time(
        tv_path=tv_nan,
        op_path=op_nan,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert summary["nan_mismatches"] == 1
    assert top[0]["column"] == "a"

    tv_none = tmp_path / "tv_no_common.csv"
    op_none = tmp_path / "op_no_common.csv"
    tv_none.write_text("time,a\n1000,1\n", encoding="utf-8")
    op_none.write_text("bar_time,b\n1000,1\n", encoding="utf-8")
    summary, _top = cli_compare._compare_rows_by_time(
        tv_path=tv_none,
        op_path=op_none,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert "no_common_columns" in summary["classification"]
    assert "no_comparable_cells" in summary["classification"]

    tv_order = tmp_path / "tv_order.csv"
    op_order = tmp_path / "op_order.csv"
    tv_order.write_text("a\n1\n", encoding="utf-8")
    op_order.write_text("b\n1\n", encoding="utf-8")
    summary, _top = cli_compare._compare_rows_by_order(
        tv_path=tv_order,
        op_path=op_order,
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert "no_common_columns" in summary["classification"]
    assert "no_comparable_cells" in summary["classification"]

    tv_order_missing = tmp_path / "tv_order_missing_trade_fields.csv"
    op_order_missing = tmp_path / "op_order_missing_trade_fields.csv"
    tv_order_missing.write_text(
        "entry_time_ms,exit_time_ms,net_profit\n"
        ",1000,1.25\n"
        ",2000,2.50\n",
        encoding="utf-8",
    )
    op_order_missing.write_text(
        "entry_time_ms,exit_time_ms,net_profit\n"
        "900,1000,1.25\n"
        "1900,2000,2.50\n",
        encoding="utf-8",
    )
    summary, top = cli_compare._compare_rows_by_order(
        tv_path=tv_order_missing,
        op_path=op_order_missing,
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert summary["status"] == "mismatch"
    assert summary["mismatch_cells"] == 2
    assert summary["total_cells"] == 6
    assert top[0]["column"] == "entry_time_ms"

    tv_order_unsorted_legs = tmp_path / "tv_order_unsorted_legs.csv"
    op_order_unsorted_legs = tmp_path / "op_order_unsorted_legs.csv"
    fields = "entry_time_ms,exit_time_ms,direction,entry_price,exit_price,qty,net_profit\n"
    tv_order_unsorted_legs.write_text(
        fields
        + "1000,2000,long,10,10,1,-0.1\n"
        + "1000,2000,long,10,11,100,10\n",
        encoding="utf-8",
    )
    op_order_unsorted_legs.write_text(
        fields
        + "1000,2000,long,10,11,100,10\n"
        + "1000,2000,long,10,10,1,-0.1\n",
        encoding="utf-8",
    )
    summary, top = cli_compare._compare_rows_by_order(
        tv_path=tv_order_unsorted_legs,
        op_path=op_order_unsorted_legs,
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    assert summary["status"] == "match"
    assert summary["mismatch_cells"] == 0
    assert top == []

    def fake_compare_rows_by_time(**kwargs):
        return (
            {
                "status": "mismatch",
                "classification": "value_mismatch",
                "mismatch_cells": 1,
                "total_cells": 1,
                "max_abs_delta": 1.0,
                "worst_column": "equity",
            },
            [{"column": "equity"}],
        )

    def fake_compare_rows_by_order(**kwargs):
        return (
            {
                "status": "mismatch",
                "classification": "row_mismatch",
                "mismatch_cells": 2,
                "total_cells": 2,
                "max_abs_delta": 2.0,
                "worst_column": "qty",
            },
            [{"column": "qty"}],
        )

    def fake_normalize(**kwargs):
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("trade_id\n", encoding="utf-8")
        return output_path

    monkeypatch.setattr(cli_compare, "_compare_rows_by_time", fake_compare_rows_by_time)
    monkeypatch.setattr(cli_compare, "_compare_rows_by_order", fake_compare_rows_by_order)
    monkeypatch.setattr(cli_compare, "_write_normalized_tv_trades", fake_normalize)
    result = cli_compare._compare_strategy_run_with_tv_exports(
        strategy_id="s1",
        run=SimpleNamespace(run_id="run-1"),
        exported={"equity": str(tmp_path / "op_equity.csv"), "trades": str(tmp_path / "op_trades.csv")},
        output_path=tmp_path / "compare-result",
        tv_chart=None,
        tv_trades=str(tmp_path / "tv_trades.csv"),
        tv_equity=str(tmp_path / "tv_equity.csv"),
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=False,
        compare_from_ms=None,
        compare_to_ms=None,
    )
    assert [row["type"] for row in result["comparisons"]] == ["equity", "trades"]
    assert [failure["type"] for failure in result["failures"]] == ["equity", "trades"]


def test_ops_systemd_service_jobs_and_workers_edges(monkeypatch):
    monkeypatch.setattr(os, "name", "nt", raising=False)
    assert cli_ops._systemd_available() is False
    monkeypatch.setattr(os, "name", "posix", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    assert cli_ops._systemd_available() is True
    monkeypatch.setattr(
        subprocess,
        "run",
        _raising(subprocess.CalledProcessError(1, ["systemctl", "--version"])),
    )
    assert cli_ops._systemd_available() is False

    runner = CliRunner()
    monkeypatch.setattr(cli_ops, "_cli_scheduler", SimpleNamespace(list_jobs=lambda: []))
    result = runner.invoke(cli_ops.jobs, ["list"])
    assert result.exit_code == 0, result.output
    assert "No jobs" in result.output

    monkeypatch.setattr(cli_ops, "_systemd_available", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _raising(subprocess.CalledProcessError(7, ["systemctl"])),
    )
    for command in ["start", "stop", "restart", "logs", "enable", "disable"]:
        result = runner.invoke(cli_ops.service, [command])
        assert result.exit_code == 1, (command, result.output)
        assert "Failed" in result.output

    class StatusResult:
        stdout = "status stdout"
        stderr = "status stderr"
        returncode = 3

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: StatusResult())
    result = runner.invoke(cli_ops.service, ["status"])
    assert result.exit_code == 0, result.output
    assert "Service is not running" in result.output

    import openpine.workers as workers_mod

    class Pool:
        def __init__(self, scheduler):
            pass

        def get_status(self):
            return {"running": False, "max_workers": 0, "active_workers": 0, "heartbeats": {}}

    monkeypatch.setattr(workers_mod, "AggregationWorkerPool", Pool)
    monkeypatch.setattr(workers_mod, "FeatureWorkerPool", Pool)
    result = runner.invoke(cli_ops.workers, ["status"])
    assert result.exit_code == 0, result.output
    assert "Heartbeats:  0" in result.output


def test_reports_known_show_csv_export_and_missing_export(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _patch_config(monkeypatch, cfg)
    runner = CliRunner()

    result = runner.invoke(cli_reports.reports, ["show", "data_coverage"])
    assert result.exit_code == 0, result.output
    assert "description: Data coverage report" in result.output
    assert "reports_dir" in result.output

    result = runner.invoke(cli_reports.reports, ["export", "worker_health", "--format", "csv"])
    assert result.exit_code == 0, result.output
    assert "id,description,status" in result.output
    assert "worker_health,Worker pool health report,available" in result.output

    result = runner.invoke(cli_reports.reports, ["export", "missing_report"])
    assert result.exit_code == 1
    assert "Report not found" in result.output
