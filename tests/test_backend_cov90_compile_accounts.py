from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from openpine.compile import adapter as ca
from openpine.gateway.routes import accounts_data as ad


class _Console:
    def __init__(self):
        self.messages=[]
    def print(self,*a,**k):
        self.messages.append(" ".join(map(str,a)))


def test_compile_adapter_helpers_and_profiles(monkeypatch, tmp_path):
    assert ca._is_visual_contract_diagnostic("P2A1507 Builtin plot has no runtime-equivalent visual output")
    assert ca._is_visual_contract_diagnostic("not lowerable under runtime_contract: builtin plotshape")
    assert not ca._is_visual_contract_diagnostic("P2A1507 builtin request.financial")
    assert ca._unsupported_request_in_source_error("x=request.financial(s)") == "unsupported request call is not production lowerable: request.financial"
    assert ca._unsupported_request_in_source_error("request.security('A','1',close)\nrequest.security_lower_tf('A','1',close)") is None

    tool = tmp_path / "pine2ast"; tool.write_text("#!/bin/sh\n")
    monkeypatch.setattr(ca.shutil, "which", lambda name: None)
    monkeypatch.setattr(ca, "TOOL_SEARCH_PATHS", [tmp_path])
    assert ca._find_tool("pine2ast") == tool
    assert ca._find_tool("missing") is None

    mod = ModuleType("m"); mod.__version__ = "1"; mod.VERSION = "2"
    assert ca._version_from_module(mod, "__version__", "VERSION") == "1"
    assert ca._version_from_module(ModuleType("n"), "missing") == "unknown"
    diag = SimpleNamespace(code="P", severity=SimpleNamespace(value="error"), message="boom")
    assert ca._diagnostic_message(diag) == "error: P: boom"
    assert ca._diagnostic_message("raw") == "raw"

    src, changed = ca._normalize_pine_v5_directive("//@version=5\nplot(close)\n")
    assert changed and "@version=6" in src
    assert ca._normalize_pine_v5_directive("plot(close)") == ("plot(close)", False)
    assert ca._is_pine_v5_version_rejection(["P2A0103 unsupported pine version 5"])
    assert not ca._is_pine_v5_version_rejection(["P2A0103 bad", "P2A9999 other"])
    assert not ca._is_pine_v5_version_rejection(["unrelated"])

    blockers = ca._production_metadata_blockers({
        "codegen_safe": False,
        "runtime_contract_safe": False,
        "parity_safe": False,
        "unsafe": True,
        "unsupported_features": ["x"],
        "unsupported_nodes": ["y"],
        "unsupported_declaration_args": ["z"],
        "import_aliases": {"lib":"x"},
        "compile_profile": "diagnostic",
    })
    assert len(blockers) >= 8
    assert ca._production_metadata_blockers({"compile_profile": "production"}) == []
    assert ca._unsupported_request_error(Exception("'request.financial'")) == "unsupported request call is not production lowerable: request.financial"
    assert ca._unsupported_request_error(Exception("ordinary")) is None
    result = ca.subprocess.CompletedProcess(["x"], 9, stdout="out", stderr="")
    assert ca._pine2ast_subprocess_errors(result)[0].endswith("9)")
    meta = ca._subprocess_compile_meta(profile=ca.CompileProfile.production(), module_name="m", strict=True, pine2ast_path=Path("p"), ast2python_path=Path("a"), adapter_status="selected")
    ca._mark_compile_meta_unsafe(meta, "r"); ca._mark_compile_meta_unsafe(meta, "r")
    assert meta["unsafe"] is True and meta["unsafe_reasons"] == ["r"]
    assert ca._profile_from_kwargs({"profile":"diagnostic"}).name == "diagnostic"
    with pytest.raises(ValueError):
        ca._profile_from_kwargs({"profile": ca.CompileProfile("production", False, False, True)})


def test_compile_adapter_library_and_subprocess_edges(monkeypatch, tmp_path):
    class APIs:
        versions={"pine2ast_version":"p","ast2python_version":"a","pinelib_contract_version":"l"}
        def parse_options(self, **kw): return SimpleNamespace()
        def parse_code(self, src, opts): return SimpleNamespace(ok=True, ast={"kind":"Program"}, diagnostics=[])
        def ast_to_json(self, ast): return json.dumps({"kind":"Program"})
        def translate_ast(self, payload, **kw): return SimpleNamespace(code="print(1)", metadata={"compile_profile":"production"}, source_map=[])
    apis = ca._LibraryApis(APIs().parse_code, APIs().parse_options, APIs().ast_to_json, APIs().translate_ast, APIs.versions)
    adapter = ca.SubprocessCompilerAdapter(prefer_library=True, fallback_to_subprocess=False)
    ok = adapter._compile_with_library(apis, "//@version=6\nplot(close)")
    assert ok.success and ok.python_code == "print(1)"

    def bad_translate(payload, **kw):
        return SimpleNamespace(code="", metadata={"unsafe": True}, source_map=[])
    bad_apis = ca._LibraryApis(APIs().parse_code, APIs().parse_options, APIs().ast_to_json, bad_translate, APIs.versions)
    blocked = adapter._compile_with_library(bad_apis, "//@version=6\nplot(close)")
    assert not blocked.success and "unsafe" in blocked.errors[0]

    def bad_parse(src, opts): return SimpleNamespace(ok=False, ast=None, diagnostics=[SimpleNamespace(code="P2A0103", severity=SimpleNamespace(value="error"), message="unsupported Pine version 5")])
    retry_apis = ca._LibraryApis(bad_parse, APIs().parse_options, APIs().ast_to_json, APIs().translate_ast, APIs.versions)
    retry = adapter._compile_with_library(retry_apis, "//@version=5\nplot(close)", profile="diagnostic")
    assert not retry.success and retry.compile_meta.get("unsafe") is True

    monkeypatch.setattr(ca, "_load_library_apis", lambda: (None, ca.LibraryAvailability(False, errors=["no libs"])))
    no_api = ca.SubprocessCompilerAdapter(prefer_library=True, fallback_to_subprocess=False).compile("x")
    assert not no_api.success and "no libs" in no_api.errors
    no_fallback = ca.SubprocessCompilerAdapter(prefer_library=False, fallback_to_subprocess=False).compile("x")
    assert not no_fallback.success and "subprocess" in no_fallback.errors[0]


def test_accounts_data_inventory_delete_and_routes(tmp_path, monkeypatch):
    default_cache = tmp_path / "cache"
    default_cache.mkdir()
    meta = {
        "key": {"instrument": {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"}, "timeframe": "1m"},
        "rows": 2,
        "first_time": 0,
        "last_time": 60_000,
    }
    (default_cache / "a.json").write_text(json.dumps(meta))
    (default_cache / "a.csv").write_text("x")
    (default_cache / "bad.json").write_text("{")
    monkeypatch.setattr(ad, "default_cache_dir", lambda: default_cache)

    db_path = tmp_path / "db.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE orders(order_id TEXT, strategy_id TEXT, symbol TEXT, status TEXT, created_at INTEGER)")
    db.execute("CREATE TABLE fills(fill_id TEXT, order_id TEXT)")
    db.execute("CREATE TABLE strategy_instances(strategy_id TEXT, name TEXT)")
    db.execute("CREATE TABLE candle_manifests(manifest_id TEXT, exchange TEXT, market_type TEXT, symbol TEXT, price_type TEXT, timeframe TEXT, min_open_time INTEGER, max_open_time INTEGER, row_count INTEGER, file_size_bytes INTEGER, partition_path TEXT, is_active INTEGER)")
    p = tmp_path / "partition.parquet"; p.write_text("x")
    db.execute("INSERT INTO candle_manifests VALUES ('m','binance','spot','BTCUSDT','trade','1m',0,60000,2,10,?,1)", (str(p),))
    db.execute("INSERT INTO orders VALUES ('o','s','BTCUSDT','open',123)")
    db.execute("INSERT INTO fills VALUES ('f','o')")
    db.execute("INSERT INTO strategy_instances VALUES ('s','Strategy')")
    db.commit(); db.close()

    class Storage:
        def __init__(self): self.db=sqlite3.connect(db_path)
        def execute(self, *a): return self.db.execute(*a)
        def transaction(self): return self.db
    state = SimpleNamespace(config=SimpleNamespace(sqlite_path=db_path, data_dir=tmp_path, data_cache_root=tmp_path/"root"), storage=Storage())

    groups = {}
    ad._merge_persistent_cache_groups(groups)
    assert groups and next(iter(groups.values()))["bar_count"] == 2
    (state.config.data_cache_root / "marketdata").mkdir(parents=True)
    mroot = state.config.data_cache_root / "marketdata"
    index = mroot / "index.sqlite"
    con = sqlite3.connect(index)
    con.execute("CREATE TABLE marketdata_segments(id TEXT, exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT, start_time INTEGER, end_time INTEGER, rows_count INTEGER, source_kind TEXT)")
    con.execute("INSERT INTO marketdata_segments VALUES ('seg','binance','spot','BTCUSDT','1m',0,60000,2,'trade_kline')")
    con.commit(); con.close()
    segdir = ad._marketdata_segment_dir(mroot, "binance", "spot", "BTCUSDT", "1m", "trade_kline")
    segdir.mkdir(parents=True); (segdir/"x.parquet").write_text("x")
    ad._merge_marketdata_segment_groups(state, groups)
    ad._merge_candle_manifest_groups(state, groups)
    inv = ad._data_series_inventory(state)
    assert inv and inv[0]["symbol"] == "BTCUSDT"
    summary = ad._data_summary(state)
    assert summary["orders"]["total"] == 1
    byid = ad._series_by_id(state)
    series = next(iter(byid.values()))
    assert ad._compact_ranges([{"from_ms": i, "to_ms": i, "rows": 1} for i in range(8)])[3]["collapsed"] == 3
    assert ad._coalesce_ranges([{"from_ms": 0, "to_ms": 0, "rows": 1, "source": "a"}, {"from_ms": 60_000, "to_ms": 60_000, "rows": 1, "source": "b"}], "1m")[0]["source"] == "a,b"
    assert ad._estimate_unique_bars([{"from_ms": None, "to_ms": None, "rows": 7}], "1m") == 7
    assert ad._estimate_bars_for_window(100, 100, "1m") == 0
    assert ad._timeframe_duration_ms("bad") == 60_000
    assert ad._freshness_status(None, "1m") == "empty"
    assert ad._database_size_bytes(state) >= 0
    assert ad._persistent_cache_size_bytes() > 0
    assert ad._candle_store_size_bytes(state) >= 0

    deleted_cache = ad._delete_persistent_cache_series(series)
    assert deleted_cache >= 1
    deleted_market = ad._delete_marketdata_segment_series(state, series)
    assert deleted_market >= 1
    deleted_manifest = ad._delete_candle_manifest_series(state, series)
    assert deleted_manifest == 1
    assert ad._delete_candle_manifest_series(state, series) == 0
    state.storage.db.close()
