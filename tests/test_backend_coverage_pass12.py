from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

import importlib

cli_main = importlib.import_module("openpine.cli.main")


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.lines.append(" ".join(str(a) for a in args))


class _Source:
    def __init__(self, *, name: str = "demo", active: str | None = "a1") -> None:
        self.id = "pine1"
        self.name = name
        self.source_text = "//@version=6\nindicator('x')"
        self.source_path = "demo.pine"
        self.source_hash = "hash"
        self.version = "1.0.0"
        self.source_type = "indicator"
        self.active_artifact_id = active
        self.created_at = 1
        self.updated_at = 2


class _Registry:
    instances: list["_Registry"] = []
    missing = False

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.source = _Source()
        self.active_set: tuple[str, str] | None = None
        self.removed: str | None = None
        self.closed = False
        _Registry.instances.append(self)

    def get_source(self, name: str):
        if self.missing or name == "missing":
            raise KeyError(name)
        return self.source

    def set_active_artifact(self, source_id: str, artifact_id: str) -> None:
        self.active_set = (source_id, artifact_id)

    def remove_source(self, name: str) -> None:
        self.removed = name

    def close(self) -> None:
        self.closed = True


class _ArtifactStore:
    artifacts: list[dict] = [
        {
            "artifact_id": "a1",
            "source_id": "pine1",
            "artifact_dir": "/tmp/artifacts/a1",
            "python_code": "print('x')",
            "compile_meta": {
                "params_hash": "abcdef1234567890",
                "saved_at": "now",
                "schema_version": "pine.ast_contract.v1",
                "created_at": 42,
            },
        },
        {"artifact_id": "a2", "source_id": "pine1", "compile_meta": {}},
    ]

    def list_artifacts(self, source_id: str):
        return list(self.artifacts)


@pytest.fixture()
def patched_pine_registry(monkeypatch):
    import openpine.artifacts as artifacts_mod
    import openpine.pine.registry as registry_mod

    _Registry.instances.clear()
    _Registry.missing = False
    monkeypatch.setattr(registry_mod, "SQLitePineSourceRegistry", _Registry)
    monkeypatch.setattr(artifacts_mod, "ArtifactStore", _ArtifactStore)
    yield


def test_cli_main_pine_artifact_commands_and_helpers(tmp_path, monkeypatch, patched_pine_registry):
    console = _Console()
    monkeypatch.setattr(cli_main, "console", console)

    assert cli_main._auto_pine_source_name(Path("001_alpha.pine")) == "po_0001_001_alpha"
    assert cli_main._auto_pine_source_name(Path("alpha.pine")) == "po_alpha"
    strategy_file = tmp_path / "s.pine"
    strategy_file.write_text("//@version=6\nstrategy('x')", encoding="utf-8")
    indicator_file = tmp_path / "i.pine"
    indicator_file.write_text("//@version=6\nstudy('x')", encoding="utf-8")
    bad_file = tmp_path / "b.pine"
    bad_file.write_text("//@version=6\nplot(close)", encoding="utf-8")
    assert cli_main._detect_pine_source_kind(strategy_file) == "strategy"
    assert cli_main._detect_pine_source_kind(indicator_file) == "indicator"
    with pytest.raises(click.ClickException):
        cli_main._detect_pine_source_kind(bad_file)

    cli_main.pine_artifacts.callback("demo")
    cli_main.pine_inspect.callback("demo")
    cli_main.pine_versions.callback("demo")
    cli_main.pine_rollback.callback("demo", None)
    cli_main.pine_rollback.callback("demo", "a2")
    cli_main.pine_rollback.callback("demo", "missing-artifact")
    cli_main.pine_activate.callback("demo", "a1")
    cli_main.pine_activate.callback("demo", "missing-artifact")

    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    old = list(_ArtifactStore.artifacts)
    _ArtifactStore.artifacts = [dict(old[0], artifact_dir=str(art_dir))]
    try:
        cli_main.pine_remove.callback("demo")
        assert not art_dir.exists()
    finally:
        _ArtifactStore.artifacts = old

    _Registry.missing = True
    cli_main.pine_artifacts.callback("missing")
    cli_main.pine_inspect.callback("missing")
    cli_main.pine_rollback.callback("missing", None)
    cli_main.pine_activate.callback("missing", "a1")
    cli_main.pine_remove.callback("missing")
    assert any("Pine source not found" in line for line in console.lines)


def test_cli_main_misc_commands_and_validation(tmp_path, monkeypatch):
    console = _Console()
    monkeypatch.setattr(cli_main, "console", console)

    assert cli_main._validate_event_schema("unknown") is False
    assert cli_main._validate_event_schema("StrategyRuntimeError") is True
    assert cli_main._check_writable_dir(tmp_path / "ok", "OK", console) is True
    blocking_file = tmp_path / "file"
    blocking_file.write_text("x", encoding="utf-8")
    assert cli_main._check_writable_dir(blocking_file / "child", "BAD", console) is False

    class _FakeScheduler:
        def __init__(self) -> None:
            pass

        def recover_stale_locks(self) -> int:
            return 2

        def list_jobs(self, status=None):
            return [SimpleNamespace(id="j1")]

    jobs_mod = types.ModuleType("openpine.jobs")
    jobs_mod.JobScheduler = _FakeScheduler
    jobs_mod.JobStatus = SimpleNamespace(FAILED="failed")
    monkeypatch.setitem(sys.modules, "openpine.jobs", jobs_mod)
    cli_main._check_job_queue_health(console)
    assert any("Recovered 2" in line for line in console.lines)

    assert cli_main._normalize_telegram_command({"command": "/x", "title": "X", "cli": "x"}) == {
        "command": "/x",
        "title": "X",
        "cli": "x",
    }
    assert cli_main._normalize_telegram_command(SimpleNamespace(name="/y", description="Y", cli_command="y"))["cli"] == "y"
    markup = cli_main._telegram_menu_markup()
    assert markup["inline_keyboard"]


def test_cli_runner_simple_top_level_and_stream_commands(monkeypatch):
    runner = CliRunner()
    result = runner.invoke(cli_main.cli, ["version"])
    assert result.exit_code == 0
    assert "openpine" in result.output

    result = runner.invoke(cli_main.cli, ["streams", "plan"])
    assert result.exit_code == 0
    assert "binance_ws" in result.output

    result = runner.invoke(cli_main.cli, ["streams", "setup"], input="c\n")
    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()

    result = runner.invoke(cli_main.cli, ["streams", "setup"], input="1\n")
    assert result.exit_code == 0
    assert "binance_ws" in result.output

    result = runner.invoke(cli_main.cli, ["events", "schema", "validate", "unknown"])
    assert result.exit_code != 0
    result = runner.invoke(cli_main.cli, ["events", "schema", "validate", "StrategyRuntimeError"])
    assert result.exit_code == 0


def test_cli_state_invalid_and_policy_paths(tmp_path, monkeypatch):
    console = _Console()
    monkeypatch.setattr(cli_main, "console", console)

    cfg = SimpleNamespace(
        state=SimpleNamespace(save_policy="interval", save_interval_bars=5, keep_last_snapshots=9),
        data_dir=tmp_path,
    )
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", staticmethod(lambda: cfg))
    cli_main._print_state_policy()
    assert any("interval" in line for line in console.lines)

    cli_main.state_invalid.callback()
    assert any("No state directory" in line for line in console.lines)

    strategy_dir = tmp_path / "state" / "strategy_id=s1"
    strategy_dir.mkdir(parents=True)
    snap = strategy_dir / "snap_1.state.msgpack"
    snap.write_bytes(b"x")
    snap.with_suffix(".debug.json").write_text(json.dumps({"last_processed_bar_time": 123}), encoding="utf-8")
    console.lines.clear()
    cli_main.state_invalid.callback()
    assert any("strategy=s1" in line for line in console.lines)


def test_core_and_plugin_command_edges(monkeypatch):
    runner = CliRunner()

    status = SimpleNamespace(name="lib", version="1", importable=True, path="/x", error=None)
    bad = SimpleNamespace(name="bad", version=None, importable=False, path=None, error="boom")
    monkeypatch.setattr("openpine.integrations.check_core_libraries", lambda: [status])
    assert runner.invoke(cli_main.cli, ["core", "check"]).exit_code == 0
    monkeypatch.setattr("openpine.integrations.check_core_libraries", lambda: [bad])
    assert runner.invoke(cli_main.cli, ["core", "check"]).exit_code != 0

    class _TelegramCfg:
        def __init__(self) -> None:
            self.enabled = False
            self.token_ref = "env:OPENPINE_TELEGRAM_TOKEN"
            self.chat_allowlist: list[str] = []

    class _Config:
        def __init__(self) -> None:
            self.plugins = SimpleNamespace(telegram=_TelegramCfg())
            self.saved = 0

        def save(self) -> None:
            self.saved += 1

    cfg = _Config()
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", staticmethod(lambda: cfg))

    result = runner.invoke(cli_main.cli, ["plugins", "enable", "telegram", "--chat-id", "42"])
    assert result.exit_code == 0
    assert cfg.plugins.telegram.enabled is True
    result = runner.invoke(cli_main.cli, ["plugins", "enable", "unknown"])
    assert result.exit_code != 0
    result = runner.invoke(cli_main.cli, ["plugins", "disable", "telegram"])
    assert result.exit_code == 0
    result = runner.invoke(cli_main.cli, ["plugins", "disable", "unknown"])
    assert result.exit_code != 0


def test_runtime_engine_adapter_and_artifact_helpers(monkeypatch, tmp_path):
    from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe
    from openpine.runtime import engine as rt

    ns = rt._PineConstantNamespace("label")
    assert ns.style_label_up == "label.style_label_up"
    runtime = rt._make_data_provider_runtime("provider", request_data_end_ms=10)
    runtime.begin_bar("bar", 3)
    runtime.end_bar()
    runtime.config.emit_diagnostic("x")
    assert runtime.data_provider == "provider"
    assert runtime.chart_bars == ["bar"]
    assert runtime.bar_index == 3

    with pytest.raises(rt.BacktestArtifactError):
        rt._validate_production_compile_artifact("a", {"compile_status": "ERROR"})
    with pytest.raises(rt.BacktestArtifactError):
        rt._validate_production_compile_artifact("a", {"compile_status": "OK", "unsafe": True, "unsafe_reasons": ["x"]})

    module_file = tmp_path / "generated_strategy.py"
    module_file.write_text(
        "class GeneratedStrategy:\n"
        "    def __init__(self, **kwargs): self.kwargs = kwargs\n"
        "    def _process_bar(self, bar): return None\n",
        encoding="utf-8",
    )
    mod = rt._load_generated_module(module_file, "src:id", "art:id")
    assert mod.label.foo == "label.foo"
    cls = rt._select_strategy_class(mod, {"class_name": "GeneratedStrategy"})
    assert cls.__name__ == "GeneratedStrategy"

    bad_file = tmp_path / "bad_strategy.py"
    bad_file.write_text("raise RuntimeError('import failed')\n", encoding="utf-8")
    with pytest.raises(rt.BacktestArtifactError):
        rt._load_generated_module(bad_file, "s", "a")
    empty = types.SimpleNamespace(__name__="empty")
    with pytest.raises(rt.BacktestArtifactError):
        rt._select_strategy_class(empty, {})

    class _Callbacks:
        def __init__(self, on_bar_end):
            self.on_bar_end = on_bar_end

    callbacks_mod = types.ModuleType("backtest_engine.models.callbacks")
    callbacks_mod.BacktestCallbacks = _Callbacks
    models_mod = types.ModuleType("backtest_engine.models")
    monkeypatch.setitem(sys.modules, "backtest_engine.models", models_mod)
    monkeypatch.setitem(sys.modules, "backtest_engine.models.callbacks", callbacks_mod)
    seen: list[tuple[int, int]] = []
    cb = rt.BacktestEngineAdapter._progress_callbacks(lambda done, total: seen.append((done, total)), 3)
    cb.on_bar_end(None, 0, None)
    cb.on_bar_end(None, 2, None)
    assert seen[-1] == (3, 3)

    class _FakeResult:
        status = "completed"
        resume_state = {"ok": True}

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeEngine:
        def __init__(self, config):
            self.config = config

        def run(self, strategy_class, **kwargs):
            self.kwargs = kwargs
            assert kwargs["params"] == {"a": 1}
            assert kwargs["runtime_kwargs"]["symbol"] == "BTCUSDT"
            assert kwargs["callbacks"] is not None
            return _FakeResult()

        @staticmethod
        def process_next_bar():
            return None

    fake_module = SimpleNamespace(BacktestConfig=_FakeConfig, BacktestEngine=_FakeEngine)
    monkeypatch.setattr(rt, "import_library", lambda name: fake_module)
    adapter = rt.BacktestEngineAdapter()
    monkeypatch.setattr(adapter, "_to_engine_bar", lambda bar: SimpleNamespace(time=bar.time))
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    bar = Bar(inst, tf, 0, 60_000, 1.0, 2.0, 0.5, 1.5, 10.0, True)
    result = adapter.run(
        type("Strategy", (), {}),
        [bar],
        rt.BacktestRunConfig(symbol="BTCUSDT", timeframe="1m", start_time=0, end_time=60_000, qty_rounding_mode="truncate", capture_plots=True),
        params={"a": 1},
        progress_callback=lambda *_: None,
        runtime_data_provider="provider",
        resume_state={"r": 1},
        effective_pre_bars=2,
    )
    assert result.status == "completed"
    assert result.bars_processed == 1
    assert result.process_next_bar_available is True


class _FakeTelegramTransport:
    def __init__(self, updates=None, fail_updates: dict | None = None) -> None:
        self.updates = updates if updates is not None else []
        self.fail_updates = fail_updates
        self.sent: list[tuple[str, str, dict | None]] = []
        self.answered: list[tuple[str, str | None, bool]] = []

    def send(self, token, chat_id, text, parse_mode="HTML", reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(ok=True, error_message=None)

    def get_updates(self, token, offset=None, timeout=0, limit=100, allowed_updates=None):
        if self.fail_updates is not None:
            return self.fail_updates
        return {"ok": True, "result": self.updates}

    def answer_callback_query(self, token, callback_query_id, text=None, show_alert=False):
        self.answered.append((callback_query_id, text, show_alert))
        return SimpleNamespace(ok=True, error_message=None)

    def get_file(self, token, file_id):
        return {"ok": True, "result": {"file_path": "files/demo.pine"}}

    def download_file(self, token, file_path):
        return b"//@version=6\nindicator('x')\n"


class _FakeKeyboards:
    def home_menu_keyboard(self): return {"k": "home"}
    def data_jobs_keyboard(self): return {"k": "data"}
    def reports_keyboard(self): return {"k": "reports"}
    def risk_keyboard(self): return {"k": "risk"}
    def strategy_actions_keyboard(self, strategy_id): return {"k": strategy_id}
    def strategy_list_keyboard(self, strategies): return {"strategies": strategies}
    def pine_list_keyboard(self, sources): return {"sources": sources}
    def confirm_delete_keyboard(self, strategy_id): return {"confirm": strategy_id}

    def map_callback_data(self, data):
        if data == "bad":
            raise RuntimeError("bad callback")
        if data in {"op:home", "op:menu", "op:strategies:list", "op:pine:list"}:
            return []
        return ["version"]

    def map_telegram_command(self, text):
        if text == "/bad":
            raise RuntimeError("bad command")
        return ["version"]


def test_telegram_plugin_and_bot_handler_edges(monkeypatch, tmp_path):
    from openpine.notifications.telegram import (
        PluginManager,
        TelegramAuthorizationError,
        TelegramBotHandler,
        TelegramCommandPlugin,
        TelegramConfigError,
        TelegramMessage,
        TelegramPluginConfig,
        TelegramUpdate,
        TransportError,
        _format_cli_output_for_html,
        _run_cli_argv,
    )

    assert _format_cli_output_for_html("") == "(no output)"
    assert "&lt;x&gt;" in _format_cli_output_for_html("<x>")
    assert _format_cli_output_for_html("a" * 5000).endswith("...")

    cfg = TelegramPluginConfig(enabled=True, chat_allowlist=["42"])
    monkeypatch.setenv("OPENPINE_TELEGRAM_TOKEN", "token")
    transport = _FakeTelegramTransport(
        updates=[
            {"update_id": 1, "message": {"chat": {"id": 42}, "from": {"id": 7}, "text": "/menu", "message_id": 5}},
            {"update_id": 2, "callback_query": {"id": "c1", "data": "op:home", "from": {"id": 7}, "message": {"chat": {"id": 42}, "message_id": 6}}},
            {"update_id": 3, "message": {"chat": {"id": 42}, "document": {"file_id": "f1", "file_name": "demo.pine"}}},
            {"update_id": 4, "message": {"chat": {"id": 43}, "text": "/menu"}},
        ]
    )
    plugin = TelegramCommandPlugin(config=cfg, transport=transport)
    assert plugin.info().enabled is True
    assert plugin.is_chat_allowed("42") is True
    with pytest.raises(TelegramAuthorizationError):
        plugin.require_update_allowed(TelegramUpdate(update_id=1, message=TelegramMessage(chat_id="43")))

    bad_plugin = TelegramCommandPlugin(config=TelegramPluginConfig(enabled=False), transport=transport)
    with pytest.raises(TelegramAuthorizationError):
        bad_plugin.require_update_allowed(TelegramUpdate(update_id=1, message=TelegramMessage(chat_id="42")))

    assert [u.update_id for u in plugin.get_updates()] == [1, 2, 3, 4]
    assert plugin.answer_callback_query("cb").ok is True
    assert plugin.get_file("f1")["ok"] is True
    assert plugin.download_file("files/demo.pine").startswith(b"//@version")
    fail_plugin = TelegramCommandPlugin(config=cfg, transport=_FakeTelegramTransport(fail_updates={"ok": False, "description": "no"}))
    with pytest.raises(TransportError):
        fail_plugin.get_updates()
    fail_plugin2 = TelegramCommandPlugin(config=cfg, transport=_FakeTelegramTransport(fail_updates={"ok": True, "result": {}}))
    with pytest.raises(TransportError):
        fail_plugin2.get_updates()
    with pytest.raises(TelegramConfigError):
        PluginManager(plugins=[object()]).load_plugins()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("openpine.notifications.telegram._run_cli_argv", lambda argv, cli_path="openpine": json.dumps([{"id": "s1", "name": "S"}]) if "list" in argv else "ok")
    handler = TelegramBotHandler(plugin, commands_module=_FakeKeyboards(), cli_path="openpine")
    assert handler._poll_once() == 4
    assert handler._offset == 5
    handler._process_update(TelegramUpdate(update_id=5, callback_query=SimpleNamespace(id="bad", data="bad", chat_id="42", message_id=None)))
    handler._process_update(TelegramUpdate(update_id=6, message=TelegramMessage(chat_id="42", text="/bad")))
    handler._process_update(TelegramUpdate(update_id=7, message=TelegramMessage(chat_id="42", document={"file_name": "bad.exe", "file_id": "f"})))
    handler._process_update(TelegramUpdate(update_id=8, message=TelegramMessage(chat_id="42", document={"file_name": "noid.pine"})))
    assert transport.sent

    class _Completed:
        returncode = 0
        stdout = "out\n"
        stderr = ""
    monkeypatch.setattr("openpine.notifications.telegram.subprocess.run", lambda *a, **k: _Completed())
    assert _run_cli_argv(["version"]) == "out"
    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "err\n"
    monkeypatch.setattr("openpine.notifications.telegram.subprocess.run", lambda *a, **k: _Failed())
    assert "Error" in _run_cli_argv(["bad"])


def test_gateway_server_lifespan_worker_modes(monkeypatch):
    from fastapi import FastAPI
    from openpine.gateway import server

    class _Rows:
        def __init__(self, rows):
            self._rows = rows
        def fetchall(self):
            return self._rows

    class _Storage:
        def __init__(self):
            self.sql: list[str] = []
            self.commits = 0
        def execute(self, sql, params=()):
            self.sql.append(sql)
            if "SELECT run_id FROM backtest_runs" in sql:
                return _Rows([("run1",), ("run2",)])
            return _Rows([])
        def commit(self):
            self.commits += 1

    class _State:
        instances: list["_State"] = []
        def __init__(self):
            self.config = SimpleNamespace(sqlite_path=Path("db.sqlite"), live_enabled=True)
            self.storage = _Storage()
            self.strategy_registry = object()
            self.orchestrator = object()
            self.artifact_store = object()
            self.state_store = object()
            self.closed = False
            _State.instances.append(self)
        def close(self):
            self.closed = True

    class _Service:
        instances: list["_Service"] = []
        def __init__(self, *args, **kwargs):
            self.started = False
            self.stopped = False
            _Service.instances.append(self)
        def start(self):
            self.started = True
        def stop(self):
            self.stopped = True

    class _Process:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.pid = 123
            self.joined = []
            self.terminated = False
            self._alive = True
        def start(self):
            pass
        def join(self, timeout=None):
            self.joined.append(timeout)
            if len(self.joined) > 1:
                self._alive = False
        def is_alive(self):
            return self._alive
        def terminate(self):
            self.terminated = True
            self._alive = False

    class _Ctx:
        def Event(self):
            return SimpleNamespace(set=lambda: None)
        def Process(self, *args, **kwargs):
            return _Process(*args, **kwargs)

    monkeypatch.setattr(server, "GatewayState", _State)
    monkeypatch.setattr(server.mp, "get_context", lambda name: _Ctx())
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "1")
    import openpine.data.periodic_fetcher as pf_mod
    import openpine.gateway.live_runner as lr_mod
    monkeypatch.setattr(pf_mod, "PeriodicBarFetcher", _Service)
    monkeypatch.setattr(lr_mod, "LiveStrategyRunner", _Service)

    app = FastAPI()
    async def _run():
        async with server.lifespan(app):
            state = app.state.gateway
            assert state.storage.commits == 1
            assert state._fetcher.started is True
            assert state._live_runner is None
            assert state._background_worker_process.pid == 123
            assert state._background_worker_process.kwargs["args"][1] is True
        assert _State.instances[-1].closed is True
        assert all(s.stopped for s in _Service.instances)
    asyncio.run(_run())

    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "0")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "0")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "1")
    _Service.instances.clear()
    _State.instances.clear()
    app_no_worker = FastAPI()

    async def _run_no_worker():
        async with server.lifespan(app_no_worker):
            state = app_no_worker.state.gateway
            assert state._background_worker_process is None
            assert state._fetcher is None
            assert state._live_runner.started is True
        assert _State.instances[-1].closed is True
        assert all(s.stopped for s in _Service.instances)

    asyncio.run(_run_no_worker())

    app2 = server.create_app()
    assert app2.title.startswith("OpenPine")
    routes = {route.path for route in app2.routes}
    assert "/health" in routes and "/" in routes


def test_gateway_optimizer_route_and_small_edges(monkeypatch):
    import asyncio
    import openpine.optimizer as opt_pkg
    from openpine.gateway.routes import optimizer as optimizer_route
    from openpine.gateway.schemas import OptimizerDryRunRequest

    class _Service:
        def validate_config(self, strategy_id, trials):
            return SimpleNamespace(strategy_id=strategy_id, trials_requested=trials, status="ok", reason=None)

    monkeypatch.setattr(opt_pkg, "OptimizerService", _Service)
    response = asyncio.run(optimizer_route.optimizer_dry_run(OptimizerDryRunRequest(strategy_id="s1", trials=3), state=SimpleNamespace()))
    assert response.status == "ok"

    class _BadService:
        def validate_config(self, strategy_id, trials):
            raise RuntimeError("bad")

    monkeypatch.setattr(opt_pkg, "OptimizerService", _BadService)
    with pytest.raises(Exception):
        asyncio.run(optimizer_route.optimizer_dry_run(OptimizerDryRunRequest(strategy_id="s1", trials=1), state=SimpleNamespace()))
