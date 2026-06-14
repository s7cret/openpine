from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace


from openpine.batch import runner as br
from openpine.batch.tv_corpus import ChartExport, ExportEntry


def _entry(tmp_path: Path, *, kind: str = "strategy") -> ExportEntry:
    root = tmp_path / f"entry_{kind}"
    root.mkdir(parents=True, exist_ok=True)
    pine = root / "source.pine"
    pine.write_text("strategy('x')\n" if kind == "strategy" else "indicator('x')\n", encoding="utf-8")
    chart = ChartExport("1m", root / "chart.csv", 2, 0, 60_000)
    return ExportEntry(3, root.name, kind, "grp", root, pine, (chart,))


def _args(**overrides):
    base = dict(
        phase="run",
        timeframe=None,
        skip_completed=False,
        force_compile=False,
        stop_on_error=False,
        root=Path("."),
        manifest=Path("manifest.csv"),
        kind="all",
        limit=None,
        start_id=None,
        ids=None,
        summary_name=None,
        errors_name="errors.jsonl",
        calculation_from="1970-01-01T00:00:00Z",
        calculation_to=None,
        provider_only_bars=True,
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_batch_runner_source_compile_register_and_process_phases(monkeypatch, tmp_path: Path):
    entry = _entry(tmp_path, kind="strategy")
    source = SimpleNamespace(id="src1", active_artifact_id=None)

    class SourceRegistry:
        def __init__(self):
            self._conn = SimpleNamespace(execute=lambda *a, **k: None, commit=lambda: None)
        def get_source(self, name):
            raise KeyError(name)
        def add_source(self, text, name):
            return SimpleNamespace(id="src-new", source_type=None, source_path=None)
        def close(self):
            pass
        def set_active_artifact(self, source_id, artifact_id):
            self.active = (source_id, artifact_id)

    monkeypatch.setattr(br, "load_source_registry", SourceRegistry)
    added_source, added = br.get_or_add_source(entry, write=True)
    assert added and added_source.source_type == "strategy"
    assert br.get_or_add_source(entry, write=False) == (None, False)

    active = SimpleNamespace(id="src", active_artifact_id="cached")
    assert br.compile_source(active, force=False)[0] == "cached"

    class StrategyRegistry:
        def __init__(self):
            self.status = []
        def list_strategies(self):
            return []
        def register_strategy(self, **kwargs):
            return SimpleNamespace(strategy_id="sid-new")
        def update_status(self, *args):
            self.status.append(args)
        def close(self):
            pass

    monkeypatch.setattr(br, "load_strategy_registry", StrategyRegistry)
    sid, created = br.ensure_strategy_instance(entry, SimpleNamespace(id="src"), "art", "1m")
    assert sid == "sid-new" and created

    existing_registry = StrategyRegistry()
    existing_registry.list_strategies = lambda: [SimpleNamespace(name=br.strategy_name(entry, "1m"), strategy_id="sid-old")]
    monkeypatch.setattr(br, "load_strategy_registry", lambda: existing_registry)
    assert br.ensure_strategy_instance(entry, SimpleNamespace(id="src"), "art", "1m") == ("sid-old", False)

    monkeypatch.setattr(br, "get_or_add_source", lambda entry, write=True: (source, True))
    monkeypatch.setattr(br, "compile_source", lambda source, force=False: ("art1", {"status": "compiled"}))
    monkeypatch.setattr(br, "ensure_strategy_instance", lambda entry, source, artifact_id, timeframe: ("sid", True))
    monkeypatch.setattr(br, "run_strategy", lambda *a, **k: {"status": "ok", "bars": 2, "trades_rows": 1, "equity_rows": 1})

    assert br.run_entry(entry, _args(phase="plan"), "batch", {}).get("status") == "planned"
    assert br.run_entry(entry, _args(phase="ingest"), "batch", {}).get("status") == "ingested"
    assert br.run_entry(entry, _args(phase="compile"), "batch", {}).get("status") == "compiled"
    assert br.run_entry(entry, _args(phase="register"), "batch", {}).get("status") == "registered"
    result = br.run_entry(entry, _args(phase="run"), "batch", {name: "rev" for name in br.LIBRARY_NAMES})
    assert result["status"] == "ok" and result["runs"][0]["status"] == "ok"

    monkeypatch.setattr(br, "compile_source", lambda source, force=False: (None, {"status": "compile_error", "errors": ["bad"]}))
    assert br.run_entry(entry, _args(phase="run"), "batch", {}).get("status") == "compile_error"


def test_batch_runner_completed_selection_meta_and_summary(tmp_path: Path):
    entry = _entry(tmp_path, kind="indicator")
    args = _args(skip_completed=True, phase="run")
    assert not br.completed_for_selection(entry, args)
    out = entry.root / "openpine_outputs" / "1m"
    out.mkdir(parents=True)
    (out / "plots.csv").write_text("x\n1\n", encoding="utf-8")
    meta = {
        "schema_version": br.RUN_META_SCHEMA_VERSION,
        "compile_profile": br.PRODUCTION_COMPILE_PROFILE,
        "run_id": "r1",
        "batch_id": "b",
        "source_id": "s",
        "strategy_or_indicator": "indicator",
        "calculation_window": {"from_ms": 0, "to_ms": 60_000},
        "export_window": {"from_ms": 0, "to_ms": 60_000},
        "library_revisions": {name: "rev" for name in br.LIBRARY_NAMES},
    }
    (out / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"schema_version": br.RUN_META_SCHEMA_VERSION, "status": "ok"}), encoding="utf-8")
    status = {"phase": "run", "status": "ok", "runs": [{"timeframe": "1m", "status": "ok"}]}
    (entry.root / "openpine_outputs" / "openpine_batch_status.json").write_text(json.dumps(status), encoding="utf-8")
    assert br.completed_for_selection(entry, args)
    (out / "fatal_error.json").write_text("{}", encoding="utf-8")
    assert not br.completed_for_selection(entry, args)
    (out / "fatal_error.json").unlink()

    # Invalid metadata variants cover validation guards.
    bad_path = out / "bad.json"
    bad_path.write_text("not-json", encoding="utf-8")
    assert not br._run_meta_valid(bad_path)
    assert not br._run_summary_valid(bad_path)
    assert not br._valid_window({"from_ms": 2, "to_ms": 1})

    run_info = {"status": "ok", "data": {"calculation_from": 1, "calculation_to": 3, "compare_from": 2, "compare_to": 4}, "plots_rows": 5}
    run_meta = br._build_run_meta(entry=entry, chart=entry.charts[0], status={"source_id": "src", "artifact_id": "art"}, run_info=run_info, batch_id="batch", library_revisions={name: "rev" for name in br.LIBRARY_NAMES})
    summary = br._build_run_summary(entry=entry, chart=entry.charts[0], run_meta=run_meta, run_info=run_info)
    assert run_meta["calculation_window"] == {"from_ms": 1, "to_ms": 3}
    assert summary["status"] == "ok"

    rows = [
        {"kind": "indicator", "charts": [{"timeframe": "1m"}], "status": "planned"},
        {"runs": [{"timeframe": "1m", "status": "ok", "bars": 2, "plots_rows": 1}]},
    ]
    assert br.summarize(rows)["stats"]
    assert br.summary_by_timeframe(rows)["1m"]["selected"] >= 1
    assert br.parse_ids("1,3-4,,") == {1, 3, 4}
    assert br.parse_ids(None) is None


def test_batch_runner_run_selected_and_main_edges(monkeypatch, tmp_path: Path, capsys):
    entry = _entry(tmp_path, kind="strategy")
    args = _args(root=tmp_path, phase="run", errors_name="errors.jsonl", stop_on_error=True)

    results = iter([
        {"id": 3, "status": "compile_error", "kind": "strategy", "charts": [{"timeframe": "1m"}]},
    ])
    monkeypatch.setattr(br, "completed_for_selection", lambda entry, args: False)
    monkeypatch.setattr(br, "run_entry", lambda *a, **k: next(results))
    out = br._run_selected_entries(args=args, selected=[entry], batch_id="b", library_revisions={}, errors_path=tmp_path / "errors.jsonl")
    assert out[0]["status"] == "compile_error"
    assert (tmp_path / "errors.jsonl").exists()

    monkeypatch.setattr(br, "load_manifest", lambda manifest, root: [entry])
    monkeypatch.setattr(br, "filter_entries", lambda *a, **k: [entry])
    monkeypatch.setattr(br, "_get_library_revisions", lambda: {name: "rev" for name in br.LIBRARY_NAMES})
    monkeypatch.setattr(br, "_run_selected_entries", lambda **kw: [{"status": "ok", "kind": "strategy", "charts": [{"timeframe": "1m"}]}])
    rc = br.main(["--root", str(tmp_path), "--manifest", str(tmp_path / "manifest.csv"), "--phase", "run", "--calculation-from", "1970-01-01T00:00:00Z"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "summary=" in captured
