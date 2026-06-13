from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.batch import runner
from openpine.batch.tv_corpus import ChartExport, ExportEntry


def _chart(tmp_path: Path, name: str = "chart_15m.csv") -> ChartExport:
    path = tmp_path / name
    path.write_text("time,open,high,low,close,volume\n1,1,2,0,1.5,10\n2,2,3,1,2.5,20\n", encoding="utf-8")
    return ChartExport(timeframe="15m", path=path, bars=2, start_ms=1000, end_ms=2000)


def _entry(tmp_path: Path, kind: str = "strategy") -> ExportEntry:
    pine = tmp_path / "source.pine"; pine.write_text(f'{kind}("x")\n', encoding="utf-8")
    return ExportEntry(export_id=7, folder="folder", kind=kind, source_group="grp", root=tmp_path, pine_path=pine, charts=(_chart(tmp_path),))


def test_batch_runner_core_helpers_and_registries(monkeypatch, tmp_path):
    entry = _entry(tmp_path); chart = entry.charts[0]
    out_root = tmp_path / "out"
    runner._write_progress(out_root, "batch", entry.export_id, "run", "active", selected_count=1, processed_count=0, summary_by_timeframe={"15m": {"ok": 1}})
    progress = json.loads((out_root / "current_progress.json").read_text())
    assert progress["current_entry_id"] == 7 and progress["summary_by_timeframe"]
    assert runner.ms_to_utc_iso(None) is None and "1970" in runner.ms_to_utc_iso(1000)
    cb = runner.build_progress_callback("x", 2); cb(1, 5); cb(2, 5); cb(5, 5)
    assert runner.build_progress_callback("x", 0) is None

    class SourceRegistry:
        created = []
        def __init__(self): self._conn = SimpleNamespace(execute=lambda *a, **k: None, commit=lambda: None)
        def get_source(self, name): raise KeyError(name)
        def add_source(self, text, name):
            src = SimpleNamespace(id="pine", name=name, active_artifact_id=None)
            self.created.append(src); return src
        def set_active_artifact(self, source_id, artifact_id): self.active=(source_id, artifact_id)
        def close(self): pass
    monkeypatch.setattr(runner, "load_source_registry", SourceRegistry)
    assert runner.get_or_add_source(entry, write=False) == (None, False)
    src, created = runner.get_or_add_source(entry, write=True)
    assert created and src.source_type == "strategy"
    src.active_artifact_id = "cached"
    assert runner.compile_source(src, force=False)[0] == "cached"

    import openpine.compile as compile_mod
    import openpine.pine.registry as registry_mod
    monkeypatch.setattr(compile_mod, "SubprocessCompilerAdapter", lambda: object())
    monkeypatch.setattr(registry_mod, "SQLitePineSourceRegistry", SourceRegistry)
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": True, "artifact_id": "art", "artifact_path": "x", "errors": []})
    src.active_artifact_id = None
    assert runner.compile_source(src, force=True)[0] == "art"
    monkeypatch.setattr(compile_mod, "compile_pipeline", lambda source, adapter: {"success": False, "errors": ["bad"]})
    assert runner.compile_source(src, force=True)[0] is None

    class StrategyRegistry:
        def __init__(self): self.items=[]
        def list_strategies(self): return self.items
        def register_strategy(self, **kw): return SimpleNamespace(strategy_id="sid")
        def update_status(self, strategy_id, status): self.status=(strategy_id,status)
        def close(self): pass
    monkeypatch.setattr(runner, "load_strategy_registry", StrategyRegistry)
    sid, made = runner.ensure_strategy_instance(entry, src, "art", "15m")
    assert sid == "sid" and made


def test_batch_runner_data_and_meta_edges(monkeypatch, tmp_path):
    entry = _entry(tmp_path); chart = entry.charts[0]
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"); tf=parse_timeframe("15m")
    bars = [Bar(instrument=inst, timeframe=tf, time=1000, time_close=2000, open=1, high=2, low=0, close=1.5, volume=1, closed=True)]
    args = argparse.Namespace(symbol="BTCUSDT", exchange="binance", market_type="spot", calculation_from="1", calculation_to="3", _calculation_to_by_timeframe={}, root=tmp_path, manifest=tmp_path / "manifest.csv", provider_only_bars=True)

    class Provider: _provider = SimpleNamespace(last_fetch_info={"source":"fake"})
    class Series: pass
    class Orch:
        def __init__(self, provider=None): pass
        def load_bars(self, query): return SimpleNamespace(bars=bars)
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", Orch)
    monkeypatch.setattr("openpine.data.provider_adapter.create_local_marketdata_provider_adapter", lambda: Provider())
    loaded, info = runner.load_calculation_bars(entry, chart, args, {})
    assert loaded == bars and info["symbol"] == "BTCUSDT"
    loaded2, info2 = runner.load_calculation_bars(entry, chart, args, {})
    assert info2["cache_hit"] is True
    args_bad = argparse.Namespace(**{**args.__dict__, "calculation_from": "3", "calculation_to": "1"})
    with pytest.raises(ValueError): runner.load_calculation_bars(entry, chart, args_bad, {})

    assert runner.chart_end_exclusive_ms(chart) > chart.end_ms
    chart.path.write_text("time,open,high,low,close,BAR_INDEX\n1,1,2,0,1.5,5\n2,2,3,1,2.5,6\n", encoding="utf-8")
    offset, meta = runner._infer_tv_bar_index_offset(chart, [SimpleNamespace(time=1000), SimpleNamespace(time=2000)])
    assert isinstance(offset, int) and meta is not None
    periodic_offset, periodic_meta = runner._infer_tv_bar_index_offset_from_periodic_na(pd.DataFrame({"A":[1, None, 3]}), first_visible_local_index=5)
    assert periodic_meta is None or isinstance(periodic_offset, int)
    rows = runner._merge_tv_visible_bars(provider_bars=bars, chart=chart, symbol="BTCUSDT", exchange="binance", market_type="spot")
    assert rows

    assert runner.result_has_error({"status": "run_error"}) and runner.result_has_error({"runs": [{"status": "bad"}]})
    assert runner._expected_output_files(entry, chart)
    assert runner._wanted_charts(entry, argparse.Namespace(timeframe=None)) == list(entry.charts)
    good = tmp_path / "good.json"; good.write_text('{"status":"ok", "bars_total":1, "output_files":{"plots_csv":"x"}}', encoding="utf-8")
    assert runner._output_file_valid(good)
    assert runner._run_meta_valid(good) is False
    assert runner._run_summary_valid(good) is False
    rid = runner._run_id("batch", entry, chart)
    assert "batch" in rid
    status = {"source_id": "pine", "artifact_id": "art"}
    run_info = {"status": "ok", "data": {"calculation_from": 1, "calculation_to": 2, "compare_from": 1, "compare_to": 2}}
    meta = runner._build_run_meta(entry=entry, chart=chart, status=status, run_info=run_info, batch_id="batch", library_revisions={name: "rev" for name in runner.LIBRARY_NAMES})
    assert meta["status"] == "ok"
    summary = runner._build_run_summary(entry=entry, chart=chart, run_meta=meta, run_info=run_info)
    assert summary["run_id"] == meta["run_id"]
    assert runner.entry_summary(entry)["id"] == 7
    assert runner.parse_ids("1,2,3") == {1,2,3}
    assert runner.parse_ids(None) is None
    results = [{"status":"ok", "charts":[{"timeframe":"15m", "status":"ok", "bars": 1}]}, {"status":"error", "charts":[{"timeframe":"15m", "status":"error", "error":"bad"}]}]
    assert runner.summarize(results)["stats"]["error"] == 1
    assert runner.summary_by_timeframe(results)["15m"]["selected"] == 2
    assert runner.resolve_calculation_to_by_timeframe([entry], argparse.Namespace(phase="plan", calculation_to=None)) == {}
