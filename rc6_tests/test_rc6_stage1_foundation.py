"""Stage 1 fault/ownership/admission tests; these are not TV conformance scores."""

from pathlib import Path

import pytest
from backtest_engine import BacktestConfig
from openpine.runtime.rc6_config import serialize_engine_config, resolve_engine_config
from openpine.runtime.effective_config import EffectiveStrategyConfig
from openpine.verification.architecture import COMPONENTS, check_architecture
from openpine.verification.capabilities import build_capability_graph, effective_target_identity
from openpine.verification.conformance import compare_corpus, first_difference, load_corpus
from openpine.verification.identity import canonical, digest, read_json, seal
from openpine.verification.pytest_gate import collection_hash, validate_inventory

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/corpus-v1/manifest.json"


@pytest.mark.parametrize(
    "name,value",
    [
        ("margin_long", 0),
        ("margin_short", 0),
        ("commission_value", 0),
        ("process_orders_on_close", False),
        ("qty_rounding", "none"),
    ],
)
def test_effective_snapshot_is_immutable_and_preserves_explicit_values(name, value):
    config = BacktestConfig("S", "1m", 0, 60000)
    setattr(config, name, value)
    payload = serialize_engine_config(config, "strict_5x")
    resolved = resolve_engine_config(payload, {"mintick": "0.01"})
    frozen = resolved.effective_strategy_config
    assert frozen.to_dict()["settings"][name] == value
    assert frozen.settings_hash == resolved.effective_config_hash
    assert frozen.to_dict()["provenance"][name][0]["source"] == "submitted"
    frozen.assert_matches(resolved)
    detached = frozen.to_dict()
    detached["settings"]["required_outputs"].clear()
    assert frozen.to_dict()["settings"]["required_outputs"]
    assert EffectiveStrategyConfig.parse(frozen.to_dict()) == frozen
    with pytest.raises(AttributeError):
        frozen._encoded = b"changed"
    resolved.initial_capital += 1
    with pytest.raises(ValueError, match="changed"):
        frozen.assert_matches(resolved)


def test_config_provenance_does_not_invent_legacy_origins():
    resolved = resolve_engine_config(
        dict(symbol="S", timeframe="1m", start_time=0, end_time=1), {"mintick": "0.25"}
    )
    trace = resolved.effective_strategy_config.to_dict()["provenance"]
    assert trace["margin_long"][0]["source"] == "engine_default"
    assert trace["mintick"][0]["source"] == "admitted_instrument"


def test_rehashed_but_inconsistent_effective_config_rejected():
    obj = resolve_engine_config(dict(symbol="S", timeframe="1m", start_time=0, end_time=1), {})
    value = obj.effective_strategy_config.to_dict()
    value["settings"]["initial_capital"] = 1
    value = seal({k: v for k, v in value.items() if k != "content_hash"})
    with pytest.raises(ValueError, match="settings hash"):
        EffectiveStrategyConfig.parse(value)


@pytest.mark.parametrize("dynamic", [False, True])
def test_every_component_obeys_ownership_and_forbidden_import_is_reported(tmp_path, dynamic):
    for name, (package, _, _) in COMPONENTS.items():
        path = tmp_path / name / package
        path.mkdir(parents=True)
        (path / "__init__.py").write_text("")
    assert check_architecture(tmp_path)["ok"]
    path = tmp_path / "pinelib/pinelib/bad.py"
    path.write_text(
        'importlib.import_module("openpine.gateway")'
        if dynamic
        else "from openpine.gateway import server"
    )
    report = check_architecture(tmp_path)
    assert not report["ok"]
    assert report["issues"][0]["code"] == "FORBIDDEN_DEPENDENCY"
    assert report["issues"][0]["line"] == 1


def test_duplicate_semantic_owner_is_not_allowed(tmp_path):
    for name, (package, _, _) in COMPONENTS.items():
        path = tmp_path / name / package
        path.mkdir(parents=True)
        (path / "__init__.py").write_text("")
    (tmp_path / "openpine/openpine/copy.py").write_text("def process_bar_fills(): pass")
    assert any(
        i["code"] == "DUPLICATED_SEMANTIC_OWNER" for i in check_architecture(tmp_path)["issues"]
    )


def test_missing_component_is_not_an_empty_success(tmp_path):
    report = check_architecture(tmp_path)
    assert not report["ok"] and len(report["issues"]) == 8


def test_capabilities_keep_unknowns_and_missing_handlers(monkeypatch):
    import openpine.verification.capabilities as module

    original = build_capability_graph()
    assert len(original["rows"]) > 6000
    assert {row["pine_version"] for row in original["rows"]} == set(range(1, 7))
    assert all(row["oracle"] == "missing" for row in original["rows"])
    assert original["counts"]["UNAVAILABLE"] and original["counts"]["UNVERIFIED"]
    surface = module.strategy_host_surface()
    surface["commands"].pop("strategy.entry")
    surface["content_hash"] = digest(surface)
    monkeypatch.setattr(module, "strategy_host_surface", lambda: surface)
    changed = build_capability_graph()
    row = next(
        r
        for r in changed["rows"]
        if r["symbol_id"] == "pine:function:strategy.entry" and r["pine_version"] == 6
    )
    assert "HOST_HANDLER_MISSING" in row["reasons"]
    assert changed["content_hash"] != original["content_hash"]


def test_effective_target_key_binds_mode_and_host():
    left = effective_target_identity(6, "sha256:" + "a" * 64)
    assert (
        left["content_hash"] != effective_target_identity(5, "sha256:" + "a" * 64)["content_hash"]
    )
    assert (
        left["content_hash"] != effective_target_identity(6, "sha256:" + "b" * 64)["content_hash"]
    )
    assert (
        left["content_hash"]
        != effective_target_identity(6, "sha256:" + "a" * 64, "bulk_backtest")["content_hash"]
    )
    assert left["oracle_status"] == "unverified"


def observations(manifest, root):
    result = {}
    for case in manifest["cases"]:
        expected = read_json(root / case["expected"]["path"])
        result[case["id"]] = {
            "status": "completed",
            **expected,
            **{key + "_sha256": case[key]["sha256"] for key in ("source", "data", "settings")},
        }
    return result


@pytest.mark.parametrize(
    "fault,code",
    [
        ("data", "DATA_MISMATCH"),
        ("source", "SOURCE_MISMATCH"),
        ("settings", "CONFIG_MISMATCH"),
        ("skip", "EXECUTION_NOT_COMPLETED"),
        ("missing", "NOT_RUN"),
        ("compile", "COMPILE_MISMATCH"),
        ("events", "BROKER_MISMATCH"),
    ],
)
def test_conformance_never_counts_missing_skips_or_mismatches_as_pass(fault, code):
    manifest = load_corpus(CORPUS)
    actual = observations(manifest, CORPUS.parent)
    cid = manifest["cases"][0]["id"]
    if fault in {"data", "source", "settings"}:
        actual[cid][fault + "_sha256"] = "0" * 64
    elif fault == "skip":
        actual[cid]["status"] = "skipped"
    elif fault == "missing":
        actual.pop(cid)
    elif fault == "compile":
        actual[cid]["compile"] = False
    else:
        actual[cid]["events"][0]["qty"] = 7
    report = compare_corpus(CORPUS, actual, expected_corpus_hash=manifest["content_hash"])
    assert report["denominator"] == 12 and report["earned"] == 11
    assert not report["ok"] and not report["critical_gate"]
    assert report["results"][0]["status"] == code
    if fault == "events":
        assert report["results"][0]["first_divergence"]["location"]["bar"] == 0


@pytest.mark.parametrize("fault", ["oracle_missing", "not_external", "shrunk_corpus"])
def test_external_claims_and_denominator_changes_are_not_silent(tmp_path, fault):
    import shutil

    shutil.copytree(CORPUS.parent, tmp_path / "corpus")
    path = tmp_path / "corpus/manifest.json"
    manifest = load_corpus(path)
    original = manifest["content_hash"]
    actual = observations(manifest, path.parent)
    if fault == "oracle_missing":
        manifest["cases"][0]["expected"] = None
    elif fault == "not_external":
        manifest["profile"] = "tradingview"
    else:
        manifest["cases"].pop()
    manifest = seal({k: v for k, v in manifest.items() if k != "content_hash"})
    path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="frozen corpus changed"):
        compare_corpus(path, actual, expected_corpus_hash=original)
    if fault != "shrunk_corpus":
        report = compare_corpus(path, actual, expected_corpus_hash=manifest["content_hash"])
        assert not report["ok"] and not report["tradingview_verified"]


def test_numeric_comparator_distinguishes_false_zero_and_missing_na():
    tol = {"absolute": 0, "relative": 0}
    assert first_difference(False, 0, tol)
    assert first_difference({"$na": True}, None, tol)
    assert first_difference({"v": None}, {}, tol)
    assert first_difference([1, 2], [2, 1], tol)
    assert first_difference(1.0, 1.0001, {"absolute": 0.001, "relative": 0}) is None


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_duplicate_or_nonfinite_artifact_json_rejected(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_json(path)


@pytest.mark.parametrize("fault", ["deleted", "duplicated", "renamed", "deselected"])
def test_required_collection_cannot_shrink_silently(fault):
    ids = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
    expected = {"count": 2, "sha256": collection_hash(ids), "deselected": 0}
    validate_inventory(ids, expected, 0)
    if fault == "deleted":
        ids.pop()
    elif fault == "duplicated":
        ids[1] = ids[0]
    elif fault == "renamed":
        ids[1] = "tests/test_c.py::test_c"
    with pytest.raises(ValueError):
        validate_inventory(ids, expected, int(fault == "deselected"))


@pytest.mark.parametrize("outcome", ["pass", "skip", "xfail", "empty"])
def test_real_pytest_gate_cannot_report_skips_xfails_or_empty_as_success(tmp_path, outcome):
    import os
    import subprocess
    import sys

    body = {
        "pass": "def test_required(): assert True\n",
        "skip": "import pytest\n@pytest.mark.skip(reason='required')\ndef test_required(): pass\n",
        "xfail": "import pytest\n@pytest.mark.xfail(reason='required')\ndef test_required(): assert False\n",
        "empty": "# no tests\n",
    }[outcome]
    (tmp_path / "test_required.py").write_text(body)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(ROOT)}
    output = tmp_path / "receipt.json"
    # Fixed test interpreter/module and private test paths; no user-supplied shell.
    run = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "openpine.verification.pytest_gate",
            "--verification-output",
            str(output),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    receipt = read_json(output)
    assert (run.returncode == 0) == (outcome == "pass"), run.stdout + run.stderr
    assert receipt["ok"] == (outcome == "pass")


def test_missing_runtime_callable_prevents_compilation(monkeypatch):
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
    import pinelib.abi.primitives as primitive

    monkeypatch.setattr(primitive, "operator_binary_v1", None)
    result = NativeRC6CompilerAdapter().compile(
        '//@version=6\nstrategy("missing primitive")\nx=close+1\n',
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )
    assert not result.success
    assert "missing runtime operation" in str(result.errors)


def test_rehashed_config_cannot_lie_about_terminal_provenance():
    config = resolve_engine_config(dict(symbol="S", timeframe="1m", start_time=0, end_time=1), {})
    value = config.effective_strategy_config.to_dict()
    value["provenance"]["initial_capital"][-1]["value"] = 1
    value = seal({k: v for k, v in value.items() if k != "content_hash"})
    with pytest.raises(ValueError, match="terminal"):
        EffectiveStrategyConfig.parse(value)


@pytest.mark.parametrize(
    "fault", ["missing_task", "duplicate_task", "bad_dependency", "missing_stage", "empty_exit"]
)
def test_stage_plan_preserves_original_scope(fault):
    from openpine.verification.stage_gate import validate_stages

    plan = read_json(ROOT / "verification/stages.json")
    ledger = read_json(ROOT / "docs/RC6_REVIEW_36.json")
    validate_stages(plan, ledger)
    if fault == "missing_task":
        plan["stages"][0]["tasks"].pop()
    elif fault == "duplicate_task":
        plan["stages"][0]["tasks"].append("OP-01")
    elif fault == "bad_dependency":
        plan["stages"][0]["depends_on"] = [8]
    elif fault == "missing_stage":
        plan["stages"].pop()
    else:
        plan["stages"][0]["exit_criteria"] = []
    with pytest.raises(ValueError):
        validate_stages(plan, ledger)


def test_missing_handler_blocks_required_capability_gate():
    from openpine.verification.stage_gate import validate_capabilities

    graph = build_capability_graph()
    policy = read_json(ROOT / "verification/capability-policy.json")
    validate_capabilities(graph, policy)
    next(
        r
        for r in graph["rows"]
        if r["symbol_id"] == "pine:function:strategy.entry" and r["pine_version"] == 6
    )["status"] = "UNAVAILABLE"
    with pytest.raises(ValueError, match="incomplete"):
        validate_capabilities(graph, policy)
