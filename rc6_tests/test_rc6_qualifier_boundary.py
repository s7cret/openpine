"""Qualifier compatibility evidence and actual compiled host admission."""

from dataclasses import replace
import hashlib
import json

import pytest

from ast2python.lowering import TargetManifest, load_pinelib_target_manifest
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.verification.builtins import build_builtin_surface, qualifier_binding_evidence
from openpine.verification.capabilities import build_capability_graph


@pytest.fixture(scope="module")
def surface():
    return build_builtin_surface()


def source(body, version=6):
    return NativeRC6CompilerAdapter().compile(
        f'//@version={version}\nstrategy("qualifier boundary")\n{body}\n',
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )


def seal_target(raw):
    raw["content_hash"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {k: v for k, v in raw.items() if k != "content_hash"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    return TargetManifest.from_mapping(raw)


@pytest.mark.parametrize("version", range(1, 7))
def test_report_does_not_confuse_binding_with_complete_qualifier_domain(surface, version):
    rows = [
        r
        for r in surface["rows"]
        if r["pine_version"] == version and r["symbol_id"] == "pine:function:ta.macd"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "RUNTIME_DIRECT"
    assert row["qualifier_contract"]["status"] == ("COMPATIBLE" if version >= 5 else "INCOMPATIBLE")
    assert row["oracle"] == "missing"
    assert not surface["tradingview_verified"]


@pytest.mark.parametrize(
    "symbol",
    [
        "ta.ema",
        "ta.macd",
        "ta.tsi",
        "ta.sma",
        "strategy.risk.max_position_size",
        "strategy.risk.allow_entry_in",
    ],
)
@pytest.mark.parametrize("version", [5, 6])
def test_modern_admitted_contracts_remain_compatible(surface, symbol, version):
    rows = [
        r
        for r in surface["rows"]
        if r["pine_version"] == version and r["symbol_id"] == "pine:function:" + symbol
    ]
    assert rows and all(
        r["qualifier_contract"] == {"status": "COMPATIBLE", "reasons": []} for r in rows
    )


def test_all_signatures_remain_in_qualifier_denominator(surface):
    assert sum(surface["qualifier_counts"].values()) == len(surface["rows"])
    assert surface["qualifier_counts"]["UNVERIFIED"] > 0
    assert surface["qualifier_counts"]["INCOMPATIBLE"] > 0
    assert not surface["full_catalog_verified"]


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_capability_graph_records_qualifiers_separately_for_every_signature(mode):
    graph = build_capability_graph(mode)
    signatures = [s for r in graph["rows"] for s in r.get("signature_bindings", [])]
    assert signatures and all("qualifier_contract" in s for s in signatures)
    row = next(
        r
        for r in graph["rows"]
        if r["symbol_id"] == "pine:function:ta.ema"
        and r["pine_version"] == 6
        and r["category"] == "functions"
    )
    assert row["status"] == "BOUND"
    assert all(s["qualifier_contract"]["status"] == "COMPATIBLE" for s in row["signature_bindings"])


@pytest.mark.parametrize(
    "body", ["x=ta.sma(close,bar_index+1)", "x=ta.sma(source=close,length=bar_index+1)"]
)
def test_host_rejects_target_mismatch_before_an_artifact_exists(monkeypatch, body):
    import openpine.compile.native_rc6 as adapter

    assert source(body).success
    raw = load_pinelib_target_manifest().to_dict()
    for b in raw["call_bindings"]:
        if b["symbol_id"] == "pine:function:ta.sma":
            b["parameter_qualifiers"]["length"] = "simple"
    narrowed = seal_target(raw)
    monkeypatch.setattr(adapter, "load_pinelib_target_manifest", lambda: narrowed)
    result = source(body)
    assert not result.success and not result.python_code and not result.generated_artifact
    assert "A2P_TARGET_ARGUMENT_QUALIFIER" in str(result.errors)


@pytest.mark.parametrize(
    "prefix,argument", [("", "2"), ("n=input.int(2)\n", "n"), ("simple int n=2\n", "n")]
)
def test_compiled_metadata_records_the_new_admission_boundary(prefix, argument):
    result = source(prefix + f"x=ta.ema(close,{argument})")
    assert result.success, result.errors
    assert result.compile_meta["normalized_target_schema"] == "ast2python.target_manifest.v2"
    assert result.compile_meta["argument_qualifier_admission"] == "producer_and_exact_target"


def test_missing_source_evidence_never_certifies_contract():
    target = load_pinelib_target_manifest()
    b = next(b for b in target.call_bindings.values() if b.symbol_id == "pine:function:ta.ema")
    assert qualifier_binding_evidence(b, 6, None)["status"] == "UNVERIFIED"
    assert qualifier_binding_evidence(None, 6, [])["status"] == "UNVERIFIED"
    missing = replace(b, parameter_qualifiers=None)
    assert (
        qualifier_binding_evidence(missing, 6, [{"name": "length", "qualifier_max": "simple"}])[
            "status"
        ]
        != "COMPATIBLE"
    )


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
def test_real_worker_consumes_artifact_with_qualified_input_and_risk(tmp_path, mode):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_deferred_exits import prepare
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = prepare(
        "n=input.int(2)\nstrategy.risk.max_position_size(n)\nx=ta.ema(close,n)\n"
        'if bar_index==2\n    strategy.entry("L",strategy.long,qty=x)\n',
        [(100, 101, 99, 100)] * 5,
    )
    compiled, context, config = case
    assert compiled.compile_meta["normalized_target_schema"] == "ast2python.target_manifest.v2"
    for key, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, key, value)
    output = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert output["ok"]
    result = output["raw_result"]
    assert [(t.entry_id, t.entry_price, t.qty) for t in result.open_trades] == [("L", 100, 2)]
