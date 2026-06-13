from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from ast2python.profiles import CompileProfile
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    StoreResult,
    parse_timeframe,
)

from openpine.compile import adapter as compile_adapter
from openpine.compile.adapter import CompileResult, SubprocessCompilerAdapter
from openpine.data import direct_data_provider, direct_provider, orchestrator as orch_mod
from openpine.data.direct_data_provider import DirectBinanceDataProvider
from openpine.data.direct_provider import DirectBinanceProvider
from openpine.data.orchestrator import (
    BarSeriesValidator,
    DataOrchestrator,
    IncompleteCoverageError,
    StorageUnavailableError,
)
from openpine.execution import ccxt_common, models as exec_models
from openpine.execution.binance import BinanceLiveExecutionAdapter
from openpine.execution.bybit import BybitLiveExecutionAdapter
from openpine.execution.models import InstrumentRules, LiveOrderResult
from openpine.execution.paper import PaperExecutionAdapter
from openpine.execution.router import ExecutionRouter
from openpine.orders.models import Order, OrderIntent, OrderSide, OrderStatus, OrderType
from openpine.accounts.models import Account, AccountType
from openpine.storage import backup as backup_mod
from openpine.storage import manifests as manifest_mod
from openpine.storage.sqlite_storage import SQLiteStorage


_INST = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
_TF = parse_timeframe("1m")


def _query(
    *,
    source: str = "auto",
    gap_policy: str = "fail",
    start_ms: int = 0,
    end_ms: int = 180_000,
    timeframe=None,
) -> BarQuery:
    return BarQuery(
        instrument=_INST,
        timeframe=timeframe or _TF,
        start_ms=start_ms,
        end_ms=end_ms,
        source=source,
        gap_policy=gap_policy,
    )


def _bar(time_ms: int = 0, close: float = 1.0, *, closed: bool = True) -> Bar:
    return Bar(
        instrument=_INST,
        timeframe=_TF,
        time=time_ms,
        time_close=time_ms + 60_000,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=10.0,
        closed=closed,
    )


def _coverage(query: BarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
    return DataOrchestrator.coverage_for_series(query, bars, source)


def _series(
    query: BarQuery | None = None,
    bars: tuple[Bar, ...] | None = None,
    *,
    source: str = "test",
) -> BarSeries:
    query = query or _query(source="storage")
    bars = bars if bars is not None else (_bar(0), _bar(60_000), _bar(120_000))
    return BarSeries(query=query, bars=bars, coverage=_coverage(query, bars, source))


def _fake_apis(
    *,
    parse_code=None,
    parse_options=None,
    ast_to_json=None,
    translate_ast=None,
    metadata: dict | None = None,
):
    metadata = metadata or {"compile_profile": "production"}
    return compile_adapter._LibraryApis(
        parse_code=parse_code
        or (lambda _source, _options: SimpleNamespace(ast={"kind": "Program"}, diagnostics=[], ok=True)),
        parse_options=parse_options or (lambda **_kwargs: SimpleNamespace()),
        ast_to_json=ast_to_json or (lambda _ast: json.dumps({"kind": "Program"})),
        translate_ast=translate_ast
        or (
            lambda *_args, **_kwargs: SimpleNamespace(
                code="# generated\n", metadata=metadata, source_map=[]
            )
        ),
        versions={
            "pine2ast_version": "test",
            "ast2python_version": "test",
            "pinelib_contract_version": "test",
            "pinelib_version": "test",
        },
    )


def _diagnostic_profile(**kwargs) -> CompileProfile:
    return CompileProfile.diagnostic(**kwargs)


def _subprocess_tools() -> compile_adapter._SubprocessTools:
    return compile_adapter._SubprocessTools(
        pine2ast_path=Path("/bin/pine2ast"), ast2python_path=Path("/bin/ast2python")
    )


def test_compile_library_loading_profile_and_fallback_boundaries(monkeypatch) -> None:
    def missing_package(name: str):
        raise RuntimeError(f"{name} unavailable for test")

    monkeypatch.setattr(compile_adapter, "_import_local_module", missing_package)
    apis, status = compile_adapter._load_library_apis()
    assert apis is None
    assert status.available is False
    assert any("pine2ast unavailable" in error for error in status.errors)

    unsafe_production = CompileProfile(
        "production",
        allow_external_library_stubs=True,
        allow_unsupported_request_stubs=False,
        allow_invalid_ast=False,
    )
    rejected = SubprocessCompilerAdapter().compile("//@version=6\n", profile=unsafe_production)
    assert rejected.success is False
    assert "production CompileProfile" in rejected.errors[0]

    unavailable = compile_adapter.LibraryAvailability(
        available=False, errors=["apis unavailable"], paths={"pine2ast": "missing"}
    )
    monkeypatch.setattr(compile_adapter, "_load_library_apis", lambda: (None, unavailable))

    calls: list[tuple[str, dict]] = []

    def fake_subprocess_compile(self, source_text: str, **kwargs) -> CompileResult:
        calls.append((source_text, kwargs))
        return CompileResult(success=True, python_code="# subprocess\n")

    monkeypatch.setattr(
        SubprocessCompilerAdapter, "_compile_with_subprocess", fake_subprocess_compile
    )
    result = SubprocessCompilerAdapter(
        prefer_library=True, fallback_to_subprocess=True
    ).compile(
        "//@version=6\n",
        profile=_diagnostic_profile(allow_subprocess_fallback=True),
    )
    assert result.success is True
    assert calls and calls[0][1]["profile"].allow_subprocess_fallback is True

    disabled = SubprocessCompilerAdapter(prefer_library=False).compile(
        "//@version=6\n", profile=CompileProfile.production()
    )
    assert disabled.success is False
    assert "subprocess compile fallback is disabled" in disabled.errors[0]



def test_compile_library_parse_error_and_exception_paths() -> None:
    adapter = SubprocessCompilerAdapter()
    apis = _fake_apis()
    unsafe = adapter._compile_with_library(
        apis,
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.production(),
        allow_external_library_stubs=True,
    )
    assert unsafe.success is False
    assert unsafe.compile_meta["compile_profile"] == "production"
    assert "unsafe compile allowances" in unsafe.errors[0]

    def parse_fails(_source, _options):
        return SimpleNamespace(
            ast=None,
            diagnostics=[
                SimpleNamespace(
                    severity=SimpleNamespace(value="error"),
                    code="P2A9999",
                    message="parse failed before lowering",
                )
            ],
            ok=False,
        )

    request_result = adapter._compile_with_library(
        _fake_apis(parse_code=parse_fails),
        "//@version=6\nindicator('x')\ny = request.financial(syminfo.tickerid, 'TOTAL_SHARES_OUTSTANDING', 'FY')\n",
        profile=CompileProfile.production(),
    )
    assert request_result.success is False
    assert request_result.errors == [
        "unsupported request call is not production lowerable: request.financial"
    ]
    assert request_result.compile_meta["production_blockers"] == request_result.errors

    generic_error = adapter._compile_with_library(
        _fake_apis(parse_options=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("api boom"))),
        "//@version=6\nindicator('x')\n",
        profile=_diagnostic_profile(),
    )
    assert generic_error.success is False
    assert generic_error.errors == ["Python compiler API failed: api boom"]



def test_compile_subprocess_parse_helpers_cover_error_returns(monkeypatch, tmp_path) -> None:
    src_path = tmp_path / "strategy.pine"
    src_path.write_text("//@version=5\nindicator('x')\n", encoding="utf-8")
    calls: list[str] = []

    def fake_run(cmd, **kwargs):
        calls.append(src_path.read_text(encoding="utf-8"))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="P2A0103 unsupported Pine version 5"
            )
        return subprocess.CompletedProcess(
            cmd, 2, stdout="still bad", stderr="retry failed"
        )

    monkeypatch.setattr(compile_adapter.subprocess, "run", fake_run)
    meta: dict = {}
    result, error = compile_adapter._parse_with_pine2ast_subprocess(
        pine2ast_path=Path("/bin/pine2ast"),
        src_path=src_path,
        source_text="//@version=5\nindicator('x')\n",
        profile=_diagnostic_profile(allow_implicit_version_rewrite=True),
        timeout=3,
        compile_meta=meta,
    )
    assert result is None
    assert error is not None
    assert error.errors == ["pine2ast failed (exit 2)", "retry failed"]
    assert calls[1].startswith("//@version=6")
    assert meta["compatibility_fallback"]["pine_version_to"] == 6

    parse_error = CompileResult(success=False, errors=["parse failed"])
    monkeypatch.setattr(
        compile_adapter,
        "_parse_with_pine2ast_subprocess",
        lambda **_kwargs: (None, parse_error),
    )
    ast_json, ast_error = compile_adapter._subprocess_ast_json_or_error(
        pine2ast_path=Path("/bin/pine2ast"),
        src_path=src_path,
        source_text="//@version=6\n",
        profile=_diagnostic_profile(allow_subprocess_fallback=True),
        timeout=3,
        compile_meta={},
    )
    assert ast_json is None
    assert ast_error is parse_error



def test_compile_subprocess_adapter_disabled_missing_tools_and_temp_write(monkeypatch) -> None:
    adapter = SubprocessCompilerAdapter(prefer_library=False)
    disabled = adapter._compile_with_subprocess(
        "//@version=6\n", profile=CompileProfile.production()
    )
    assert disabled.success is False
    assert disabled.compile_meta == {"compile_profile": "production"}

    monkeypatch.setattr(
        compile_adapter, "_resolve_subprocess_tools", lambda: (None, ["no pine2ast"])
    )
    missing = adapter._compile_with_subprocess(
        "//@version=6\n", profile=_diagnostic_profile(allow_subprocess_fallback=True)
    )
    assert missing.success is False
    assert missing.errors == ["no pine2ast"]

    monkeypatch.setattr(
        compile_adapter, "_resolve_subprocess_tools", lambda: (_subprocess_tools(), [])
    )
    monkeypatch.setattr(
        compile_adapter,
        "_write_temp_pine_source",
        lambda _source: (_ for _ in ()).throw(OSError("disk full")),
    )
    write_failed = adapter._compile_with_subprocess(
        "//@version=6\n", profile=_diagnostic_profile(allow_subprocess_fallback=True)
    )
    assert write_failed.success is False
    assert write_failed.errors == ["Failed to write temp source: disk full"]



def test_compile_subprocess_adapter_success_timeout_oserror_and_cleanup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        compile_adapter, "_resolve_subprocess_tools", lambda: (_subprocess_tools(), [])
    )
    adapter = SubprocessCompilerAdapter(timeout=7, prefer_library=False)
    profile = _diagnostic_profile(allow_subprocess_fallback=True)

    run_calls: list[list[str]] = []

    def successful_run(cmd, **kwargs):
        run_calls.append([str(part) for part in cmd])
        if cmd[1] == "parse":
            return subprocess.CompletedProcess(cmd, 0, stdout='{"kind":"Program"}', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="# translated\n", stderr="")

    monkeypatch.setattr(compile_adapter.subprocess, "run", successful_run)
    success = adapter._compile_with_subprocess(
        "//@version=6\n", profile=profile, module_name="covered", strict=True
    )
    assert success.success is True
    assert success.python_code == "# translated\n"
    assert success.compile_meta["adapter_status"] == "selected"
    assert any("--strict" in call for call in run_calls)

    def timeout_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(compile_adapter.subprocess, "run", timeout_run)
    timed_out = adapter._compile_with_subprocess("//@version=6\n", profile=profile)
    assert timed_out.success is False
    assert timed_out.errors == ["Compile timed out after 7s"]

    def oserror_run(cmd, **kwargs):
        raise OSError("process spawn failed")

    monkeypatch.setattr(compile_adapter.subprocess, "run", oserror_run)
    os_failed = adapter._compile_with_subprocess("//@version=6\n", profile=profile)
    assert os_failed.success is False
    assert os_failed.errors == ["Subprocess OSError: process spawn failed"]

    src_path = tmp_path / "src.pine"
    src_path.write_text("//@version=6\n", encoding="utf-8")
    monkeypatch.setattr(compile_adapter, "_write_temp_pine_source", lambda _source: src_path)
    ast_error = CompileResult(success=False, errors=["bad ast"])
    monkeypatch.setattr(
        compile_adapter,
        "_subprocess_ast_json_or_error",
        lambda **_kwargs: (None, ast_error),
    )
    assert adapter._compile_with_subprocess("//@version=6\n", profile=profile) is ast_error

    src_path.write_text("//@version=6\n", encoding="utf-8")
    ast_path = tmp_path / "ast.json"
    ast_path.write_text("{}", encoding="utf-8")
    translate_error = CompileResult(success=False, errors=["translation failed"])
    monkeypatch.setattr(
        compile_adapter,
        "_subprocess_ast_json_or_error",
        lambda **_kwargs: ("{}", None),
    )
    monkeypatch.setattr(
        compile_adapter,
        "_translate_ast_with_subprocess",
        lambda **_kwargs: (None, translate_error, ast_path),
    )
    assert adapter._compile_with_subprocess("//@version=6\n", profile=profile) is translate_error

    class UnlinkRaises:
        def unlink(self, missing_ok=False):
            raise OSError("cleanup failed")

    monkeypatch.setattr(compile_adapter, "_write_temp_pine_source", lambda _source: UnlinkRaises())
    monkeypatch.setattr(
        compile_adapter,
        "_subprocess_ast_json_or_error",
        lambda **_kwargs: ("{}", None),
    )
    monkeypatch.setattr(
        compile_adapter,
        "_translate_ast_with_subprocess",
        lambda **_kwargs: ("# ok\n", None, UnlinkRaises()),
    )
    cleanup = adapter._compile_with_subprocess("//@version=6\n", profile=profile)
    assert cleanup.success is True
    assert cleanup.python_code == "# ok\n"



class _Store:
    def __init__(self, series: BarSeries | None = None, *, write_success: bool = True):
        self.series = series
        self.write_success = write_success
        self.writes: list[BarSeries] = []

    def read(self, query: BarQuery) -> BarSeries:
        return self.series or _series(query, (), source="storage")

    def coverage(self, query: BarQuery) -> CoverageReport:
        return self.read(query).coverage

    def write(self, series: BarSeries) -> StoreResult:
        self.writes.append(series)
        return StoreResult(
            success=self.write_success,
            rows_written=len(series.bars),
            error=None if self.write_success else "write failed",
        )


class _Provider:
    def __init__(self, series: BarSeries):
        self.series = series
        self.calls: list[BarQuery] = []

    def fetch_bars(self, query: BarQuery) -> BarSeries:
        self.calls.append(query)
        return self.series



def test_data_orchestrator_set_provider_auto_storage_cache_and_getters(monkeypatch) -> None:
    query = _query(source="auto", end_ms=180_000)
    complete = _series(query, (_bar(0), _bar(60_000), _bar(120_000)), source="storage")
    provider = _Provider(complete)
    orchestrator = DataOrchestrator(store=_Store(complete), cache_enabled=False)
    orchestrator.set_provider(provider)
    loaded = orchestrator.load_bars(query)
    assert loaded is complete
    assert provider.calls == []
    assert [bar.time for bar in orchestrator.get_bars(_query(source="storage"))] == [
        0,
        60_000,
        120_000,
    ]

    bad_source_query = SimpleNamespace(
        instrument=_INST,
        timeframe=_TF,
        start_ms=0,
        end_ms=60_000,
        source="csv",
        gap_policy="fail",
        error_policy="raise",
    )
    with pytest.raises(ValueError, match="unsupported data source"):
        orchestrator.load_bars(bad_source_query)

    fallback_store = _Store(
        _series(_query(source="storage", end_ms=60_000), (_bar(0),), source="storage")
    )
    assert DataOrchestrator(store=fallback_store, cache_enabled=False).latest_bar_time(
        _query(source="storage", end_ms=60_000)
    ) == 60_000

    coverage = CoverageReport(
        requested_start_ms=0,
        requested_end_ms=120_000,
        delivered_start_ms=0,
        delivered_end_ms=60_000,
        missing_intervals=((60_000, 120_000),),
        duplicate_timestamps=(),
        source_mix=("storage",),
        status="gap",
    )

    class CoverageOnlyStore(_Store):
        def coverage(self, query: BarQuery) -> CoverageReport:
            return coverage

    gaps = DataOrchestrator(store=CoverageOnlyStore(), cache_enabled=False).detect_gaps(
        _query(source="storage", end_ms=120_000)
    )
    assert gaps[0].gap_start == 60_000
    assert gaps[0].gap_end == 120_000
    assert gaps[0].gap_id.startswith("gap_binance:spot:BTCUSDT")

    validator = DataOrchestrator(store=_Store(complete), cache_enabled=False).validate_coverage(
        complete
    )
    assert validator.status == "valid"

    monkeypatch.setattr(
        orch_mod,
        "save_bar_series",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )
    DataOrchestrator(store=_Store(complete), cache_enabled=True)._save_cache(complete)



def test_data_orchestrator_validator_and_private_edges(monkeypatch) -> None:
    query = _query(source="storage", end_ms=120_000)
    incomplete = _series(query, (_bar(0),), source="storage")
    with pytest.raises(IncompleteCoverageError, match="missing bar intervals"):
        BarSeriesValidator().validate(incomplete, allow_gaps=False)

    monkeypatch.setattr(
        orch_mod.inspect,
        "signature",
        lambda _callable: (_ for _ in ()).throw(TypeError("no signature")),
    )
    assert orch_mod._accepts_progress_callback(lambda query: None) is False

    empty_cov = DataOrchestrator.coverage_for_series(query, (), "empty-test")
    assert empty_cov.status == "empty"
    assert empty_cov.missing_intervals == ((0, 120_000),)
    assert orch_mod._coalesce_intervals(()) == ()

    no_duration_query = SimpleNamespace(
        start_ms=0, end_ms=120_000, timeframe=SimpleNamespace(duration_ms=None)
    )
    assert orch_mod._missing_intervals(no_duration_query, (_bar(0),)) == ()

    with pytest.raises(StorageUnavailableError, match="write failed"):
        DataOrchestrator(store=_Store(write_success=False), cache_enabled=False).store_bars(
            _series(query, (_bar(0), _bar(60_000)), source="storage")
        )



class _HTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _kline(open_time: int) -> list:
    return [open_time, "1", "2", "0.5", "1.5", "10"]



def test_direct_binance_provider_cache_empty_fetch_and_pagination(monkeypatch) -> None:
    assert DirectBinanceProvider.estimate_bars(_query(), start_ms=100, end_ms=99) == 0

    query = _query(source="provider", gap_policy="allow_with_metadata", end_ms=60_000)
    cached = _series(query, (_bar(0),), source="provider")
    monkeypatch.setattr(DirectBinanceProvider, "get_earliest_open_time", lambda self, q: None)
    monkeypatch.setattr(direct_provider, "cache_enabled_by_env", lambda: True)
    monkeypatch.setattr(direct_provider, "load_bar_series", lambda *_args: cached)
    progress: list[tuple] = []
    assert DirectBinanceProvider().fetch_bars(query, progress_callback=lambda *a: progress.append(a)) is cached
    assert [entry[-1] for entry in progress] == ["cache_lookup", "cache_hit"]

    monkeypatch.setattr(direct_provider, "cache_enabled_by_env", lambda: False)
    monkeypatch.setattr(
        direct_provider.urllib.request, "urlopen", lambda *_args, **_kwargs: _HTTPResponse([])
    )
    empty = DirectBinanceProvider().fetch_bars(query)
    assert empty.bars == ()
    assert empty.coverage.status == "empty"

    saved: list[BarSeries] = []
    page_calls = {"count": 0}

    def paged_urlopen(_req, timeout=15):
        page_calls["count"] += 1
        return _HTTPResponse([_kline((page_calls["count"] - 1) * 60_000)])

    monkeypatch.setattr(direct_provider, "BINANCE_PAGE_LIMIT", 1)
    monkeypatch.setattr(direct_provider, "cache_enabled_by_env", lambda: True)
    monkeypatch.setattr(direct_provider, "load_bar_series", lambda *_args: None)
    monkeypatch.setattr(direct_provider, "save_bar_series", lambda _dir, series: saved.append(series))
    monkeypatch.setattr(direct_provider.urllib.request, "urlopen", paged_urlopen)
    progress.clear()
    fetched = DirectBinanceProvider().fetch_bars(
        _query(source="provider", gap_policy="allow_with_metadata", end_ms=600_000),
        progress_callback=lambda *a: progress.append(a),
    )
    assert len(fetched.bars) == 10
    assert page_calls["count"] == 10
    assert "fetch" in [entry[-1] for entry in progress]
    assert saved and saved[-1].bars == fetched.bars



def test_direct_binance_data_provider_fallback_interval_empty_and_cursor_break(monkeypatch) -> None:
    assert direct_data_provider._to_binance_interval("foo") == "foo"
    monkeypatch.setattr(
        direct_data_provider.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse([]),
    )
    assert DirectBinanceDataProvider(timeout=1).get_bars("BTCUSDT", "1m", 0, 10) == []

    full_page = [_kline(0) for _ in range(1000)]
    monkeypatch.setattr(
        direct_data_provider.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(full_page),
    )
    bars = DirectBinanceDataProvider(timeout=1).get_bars(
        "BTCUSDT", "1s", 0, 1000, max_bars=1
    )
    assert len(bars) == 1
    assert bars[0].time == 0



def _intent(**overrides) -> OrderIntent:
    values = dict(
        client_order_id="client-1",
        strategy_id="strategy-1",
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1.0,
        price=10.0,
        stop_price=None,
    )
    values.update(overrides)
    return OrderIntent(**values)



def test_execution_models_ccxt_and_live_adapter_edge_paths(monkeypatch) -> None:
    rules = InstrumentRules(
        symbol="BTCUSDT",
        tick_size=0.5,
        step_size=0.1,
        min_qty=1.0,
        min_notional=20.0,
        max_qty=2.0,
        market_order_supported=False,
    )
    assert rules._is_aligned_float(1.0, 0) is False
    assert rules._is_aligned_decimal(1.0, 0) is False
    monkeypatch.setattr(
        exec_models,
        "Decimal",
        lambda _value: (_ for _ in ()).throw(exec_models.InvalidOperation()),
    )
    assert rules._is_aligned_decimal(1.0, 0.1) is True
    assert rules.validate_notional(1.0, None) == (True, None)
    assert rules.validate_order(0.5, 10.0, "limit")[0] is False
    assert rules.validate_order(1.0, 10.25, "limit")[0] is False
    assert rules.validate_order(1.0, 10.0, "limit")[0] is False
    assert rules.validate_order(1.0, 10.0, "market")[0] is False

    class CreateFetchClient:
        def __init__(self):
            self.created = None

        async def create_order(self, **kwargs):
            self.created = kwargs
            return {"id": "ex1", "status": "new", "filled": 0, "average": "0"}

        async def fetch_order(self, **kwargs):
            return None

        async def fetch_open_orders(self):
            return [{"id": "ex2", "symbol": "BTCUSDT", "status": "new"}]

    client = CreateFetchClient()
    live = BinanceLiveExecutionAdapter(client=client, instrument_rules={"BTCUSDT": rules})
    assert live.client is client
    assert live._validate_order(_intent(symbol="ETHUSDT"))[0] is False
    result = asyncio.run(
        ccxt_common.CcxtOrderClientMixin._call_create_order(
            live,
            _intent(order_type=OrderType.STOP, stop_price=9.5, client_order_id="cli-stop"),
        )
    )
    assert result.success is True
    assert client.created["params"] == {"clientOrderId": "cli-stop", "stopPrice": "9.5"}
    assert asyncio.run(ccxt_common.CcxtOrderClientMixin._call_get_order(live, "ex1", "BTCUSDT")) is None
    reconciled = asyncio.run(ccxt_common.CcxtOrderClientMixin._call_reconcile(live, "acct-1"))
    assert reconciled[0].order_id == "ex2"
    assert live._get_tracked_symbol("ex2") == "BTCUSDT"

    for adapter_cls in (BinanceLiveExecutionAdapter, BybitLiveExecutionAdapter):
        adapter = adapter_cls()
        adapter.add_instrument_rules(rules)
        assert adapter.get_instrument_rules("BTCUSDT") is rules
        assert adapter._parse_client_response(None).error == "Empty response"
        partial = adapter._result_to_order(
            LiveOrderResult(success=True, order_id="ex", status="partial", filled_qty=0.5),
            _intent(),
            "local",
            123,
        )
        assert partial.status == OrderStatus.PARTIAL
        failed = adapter._result_to_order(
            LiveOrderResult(success=False, error="exchange rejected"),
            _intent(),
            "local-fail",
            123,
        )
        assert failed.status == OrderStatus.REJECTED
        assert failed.error == "exchange rejected"

    class EmptyCancelClient:
        async def cancel_order(self, **_kwargs):
            return None

    assert asyncio.run(
        BinanceLiveExecutionAdapter(client=EmptyCancelClient())._call_cancel_order("o1", "BTCUSDT")
    ).success is False
    assert asyncio.run(
        BybitLiveExecutionAdapter(client=EmptyCancelClient())._call_cancel_order("o1", "BTCUSDT")
    ).success is False



def test_paper_router_and_storage_error_edges(tmp_path, monkeypatch) -> None:
    paper = PaperExecutionAdapter()
    pending = Order(
        order_id="pending-1",
        client_order_id="client-pending",
        strategy_id="strategy-1",
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1.0,
        price=10.0,
        stop_price=None,
        status=OrderStatus.NEW,
    )
    paper._orders[pending.order_id] = pending
    assert asyncio.run(paper.cancel_order("pending-1")) is True
    assert pending.status == OrderStatus.CANCELLED
    paper._orders["other"] = Order(**{**pending.__dict__, "order_id": "other", "account_id": "acct-2"})
    assert [order.account_id for order in paper.get_orders("acct-1")] == ["acct-1"]

    account = Account(
        account_id="acct-1",
        name="Paper",
        provider="local",
        exchange="binance",
        account_type=AccountType.PAPER,
    )

    class Accounts:
        def __init__(self, found=True):
            self.found = found

        def get_account(self, account_id: str):
            return account if self.found else None

    class Risk:
        def check_order(self, order, account):
            return True, None

    class RaisingAdapter:
        async def submit_order(self, order):
            raise RuntimeError("submit down")

        async def cancel_order(self, order_id):
            return True

        async def get_order_status(self, order_id):
            return None

    router = ExecutionRouter(Risk(), Accounts())
    router.register_adapter(AccountType.PAPER, RaisingAdapter())
    rejected = asyncio.run(router.submit_order(_intent(order_type=OrderType.MARKET, price=None)))
    assert rejected.status == OrderStatus.REJECTED
    assert rejected.error == "Adapter error: submit down"
    assert asyncio.run(ExecutionRouter(Risk(), Accounts(found=False)).cancel_order("o", "missing")) is False

    monkeypatch.setattr(
        manifest_mod.OpenPineConfig,
        "load",
        lambda: SimpleNamespace(config_dir=tmp_path / "config"),
    )
    default_store = manifest_mod.ManifestStore()
    assert default_store.manifest_dir == tmp_path / "config" / "manifests"

    bad_store = manifest_mod.ManifestStore(tmp_path / "manifests")
    monkeypatch.setattr(
        manifest_mod.json,
        "dump",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("json failed")),
    )
    with pytest.raises(RuntimeError, match="json failed"):
        bad_store.save_manifest("strategy-1", {"id": "strategy-1"})
    assert bad_store.list_manifests() == []

    from openpine.config import OpenPineConfig

    monkeypatch.setattr(
        OpenPineConfig,
        "load",
        lambda: SimpleNamespace(sqlite_path=tmp_path / "default.sqlite"),
    )
    storage = SQLiteStorage()
    storage.close()
    with pytest.raises(RuntimeError, match="Storage is closed"):
        _ = storage.conn

    storage = SQLiteStorage(tmp_path / "tx.sqlite")
    with pytest.raises(RuntimeError, match="rollback me"):
        with storage.transaction():
            storage.execute("CREATE TABLE rolled_back(x INTEGER)")
            raise RuntimeError("rollback me")
    storage.close()

    cfg_path = tmp_path / "config.yaml"
    cfg = SimpleNamespace(
        sqlite_path=tmp_path / "openpine.sqlite",
        duckdb_path=tmp_path / "openpine.duckdb",
        data_dir=tmp_path / "data",
        config_path=lambda: cfg_path,
        model_dump=lambda: {"token": "secret", "nested": {"api_key": "key"}},
    )
    verify_missing = backup_mod.verify_openpine(cfg)
    assert verify_missing["sqlite_exists"] is False
    cfg.sqlite_path.write_text("not sqlite", encoding="utf-8")
    monkeypatch.setattr(
        backup_mod.sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad sqlite")),
    )
    verify_bad = backup_mod.verify_openpine(cfg)
    assert verify_bad["sqlite_integrity"] is False
    with pytest.raises(RuntimeError, match="SQLite checkpoint failed"):
        backup_mod._checkpoint_sqlite(cfg.sqlite_path)
    with pytest.raises(FileNotFoundError):
        backup_mod.restore_openpine(tmp_path / "missing.tar.gz", tmp_path / "restore")
