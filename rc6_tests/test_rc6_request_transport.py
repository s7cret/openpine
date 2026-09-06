"""The existing manifest survives bounded transport before the protocol begins."""

from copy import deepcopy
import io
import json
import pytest
from openpine.runtime.request_transport import (
    RequestPreload,
    receive_request_manifest,
    inflate_request_config,
    FRAME_BYTES,
)
from openpine.runtime.rc6_config import serialize_engine_config
from rc6_tests.test_rc6_requests import case_for, ID


def fixture():
    case, _ = case_for(f'x=request.security("{ID}","5",close)')
    return case[1], serialize_engine_config(case[2], "strict_5x")


def frames(config):
    with RequestPreload(config["request_manifest"]) as stream:
        return dict(stream.descriptor), list(stream.frames())


def test_manifest_roundtrip_stops_exactly_before_normal_protocol():
    ctx, cfg = fixture()
    desc, lines = frames(cfg)
    pipe = io.StringIO("\n".join(lines) + '\n{"kind":"LOAD_ARTIFACT"}\n')
    assert receive_request_manifest(pipe, desc, ctx) == cfg["request_manifest"]
    assert json.loads(pipe.readline())["kind"] == "LOAD_ARTIFACT"
    assert all(len(s.encode()) <= FRAME_BYTES for s in lines)


def test_bootstrap_restores_identical_effective_config():
    ctx, cfg = fixture()
    desc, lines = frames(cfg)
    request = {
        "execution_context": ctx,
        "engine_config": {k: v for k, v in cfg.items() if k != "request_manifest"},
        "request_preload": desc,
    }
    inflate_request_config(request, io.StringIO("\n".join(lines) + "\n"))
    assert request["engine_config"] == cfg


@pytest.mark.parametrize(
    "fault",
    [
        "truncate",
        "order",
        "duplicate",
        "bytes",
        "digest",
        "counter_bool",
        "finish",
        "foreign",
        "size_bool",
        "config",
    ],
)
def test_corrupt_preload_never_publishes_restored_config(fault):
    ctx, cfg = fixture()
    desc, lines = frames(cfg)
    if fault == "truncate":
        lines = lines[:-1]
    elif fault == "order":
        lines.reverse()
    elif fault == "duplicate":
        lines.insert(1, lines[0])
    elif fault == "bytes":
        x = json.loads(lines[0])
        x["data"] = "%%%%"
        lines[0] = json.dumps(x)
    elif fault == "digest":
        x = json.loads(lines[0])
        x["sha256"] = "a" * 64
        lines[0] = json.dumps(x)
    elif fault == "counter_bool":
        x = json.loads(lines[0])
        x["sequence"] = False
        lines[0] = json.dumps(x)
    elif fault == "finish":
        x = json.loads(lines[-1])
        x["chunks"] += 1
        lines[-1] = json.dumps(x)
    elif fault == "foreign":
        desc["execution_context_hash"] = "sha256:" + "f" * 64
    elif fault == "size_bool":
        desc["size"] = True
    request = {
        "execution_context": ctx,
        "engine_config": {k: v for k, v in cfg.items() if k != "request_manifest"},
        "request_preload": desc,
    }
    if fault == "config":
        request["engine_config"]["initial_capital"] = 12345
    before = deepcopy(request)
    with pytest.raises(ValueError):
        inflate_request_config(request, io.StringIO("\n".join(lines) + "\n"))
    assert request == before


def test_duplicate_keys_are_not_silently_overwritten():
    ctx, cfg = fixture()
    desc, lines = frames(cfg)
    lines[0] = lines[0].replace('"sequence":0', '"sequence":0,"sequence":0')
    with pytest.raises(ValueError, match="duplicate"):
        receive_request_manifest(io.StringIO("\n".join(lines) + "\n"), desc, ctx)


def test_spool_rolls_over_and_long_values_use_bounded_frames(monkeypatch):
    import openpine.runtime.request_transport as module

    monkeypatch.setattr(module, "SPOOL_BYTES", 1024)
    ctx, cfg = fixture()
    manifest = deepcopy(cfg["request_manifest"])
    # Buffer behavior only; arbitrary data is never admitted as market data.
    manifest["diagnostic"] = "文" * 100000
    with RequestPreload(manifest) as preload:
        assert preload._spool._rolled
        lines = list(preload.frames())
        assert len(lines) > 2 and all(len(line.encode()) <= FRAME_BYTES for line in lines)


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_worker_nested_streamed_requests(tmp_path, mode):
    from rc6_tests.test_rc6_nested_integration import nested_case
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, candles = nested_case()
    compiled, ctx, cfg = case
    for name, value in dict(
        execution_context=ctx,
        admitted_manifest=_manifest(),
        instrument_id=ID,
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=candles,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(cfg, name, value)
    result = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in candles], config=cfg, params={}
    )
    assert result["ok"] and result["bars_processed"] == len(candles)
    assert [(e["command_id"], e["bar_index"]) for e in result["intent_tape"]] == [("nested", 9)]
    assert result["raw_result"].open_trades[0].qty == 1


def test_total_source_bar_budget_applies_across_datasets(monkeypatch):
    import openpine.runtime.request_data as module
    from rc6_tests.test_rc6_requests import source_rows

    ctx, _ = fixture()
    monkeypatch.setattr(module, "MAX_SOURCE_BARS", 5)
    other = source_rows(instrument_id="binance:spot:BTCUSDT", tickerid="BINANCE:BTCUSDT")
    with pytest.raises(ValueError, match="total bar count"):
        module.build_request_manifest(ctx, [source_rows(), other])


def test_valid_multiframe_manifest_reconstructs_exact_data(monkeypatch):
    import openpine.runtime.request_transport as module
    from openpine.runtime.request_data import build_request_manifest
    from rc6_tests.test_rc6_requests import source_rows

    ctx, _ = fixture()
    manifest = build_request_manifest(ctx, [source_rows(prices=tuple(range(100, 150)))])
    monkeypatch.setattr(module, "CHUNK_BYTES", 1024)
    with RequestPreload(manifest) as p:
        lines = list(p.frames())
        assert len(lines) > 20
        assert (
            receive_request_manifest(io.StringIO("\n".join(lines) + "\n"), p.descriptor, ctx)
            == manifest
        )
