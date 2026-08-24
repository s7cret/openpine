from __future__ import annotations

import json
import inspect
import threading
from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError, seal_content_hash, validate_payload, verify_content_hash

from openpine.admission import DeploymentAdmissionIdentity
from tests.admission_helpers import make_sealed_artifact

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "8" * 64
STACK_COMPONENTS = (
    "openpine-contracts",
    "marketdata-provider",
    "pinelib",
    "pine2ast",
    "ast2python",
    "backtest_engine",
    "optimizer",
    "openpine",
)


def _deployment() -> DeploymentAdmissionIdentity:
    return DeploymentAdmissionIdentity(
        stack_id="5.0.0-rc.4",
        stack_manifest_hash=HASH_A,
        wheel_identities=(("openpine", "5.0.0rc4", HASH_B),),
        schema_hashes={"openpine.run.v2": HASH_C},
        capabilities=frozenset(
            {
                "closed_bar",
                "deterministic_clock",
                "isolated_worker",
                "broker_projection",
                "intent_tape_v2",
            }
        ),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )


def test_execution_context_is_derived_from_exact_deployment_and_manifest() -> None:
    from openpine.run_identity import execution_context_from_admission

    schema_ids = (
        "openpine.execution_context.v1",
        "openpine.intent.v2",
        "openpine.worker.protocol.v2",
        "openpine.checkpoint.v1",
        "openpine.checkpoint.proof.v1",
    )
    deployment = DeploymentAdmissionIdentity(
        stack_id="5.0.0-rc.4",
        stack_manifest_hash=HASH_A,
        wheel_identities=tuple(
            (name, "5.0.0rc4", HASH_B) for name in STACK_COMPONENTS
        ),
        schema_hashes={name: HASH_C for name in schema_ids},
        capabilities=frozenset(
            {"closed_bar", "deterministic_clock", "broker_projection"}
        ),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )
    commits = {
        name: f"{index + 1:x}" * 40 for index, name in enumerate(STACK_COMPONENTS)
    }
    manifest = {
        "manifest_hash": HASH_A,
        "components": {
            name: {"sha": commits[name]} for name in STACK_COMPONENTS
        },
    }
    artifact = make_sealed_artifact(
        python_code="class GeneratedStrategy: pass\n",
        producer_commits={
            name: commits[name]
            for name in ("openpine-contracts", "pine2ast", "ast2python", "pinelib")
        },
    )
    generated = artifact["generated_artifact"]
    assert isinstance(generated, dict)

    context = execution_context_from_admission(
        deployment,
        manifest,
        run_id="run-1",
        strategy_id="strategy-1",
        artifact=artifact,
        data_snapshot_hash=HASH_D,
        series_id="binance/spot/BTCUSDT:1m",
        instrument_id="binance/spot/BTCUSDT",
        exchange="binance",
        market="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        semantic_profile="strict_5x",
        created_at_utc_ms=1,
    )

    validate_payload("openpine.execution_context.v1", context)
    assert verify_content_hash(context, schema_id="openpine.execution_context.v1")
    assert context["producer_version"] == "5.0.0-rc.4"
    assert context["producer_commit"] == commits["openpine"]
    assert context["producer_commits"] == commits
    assert context["generated_artifact_hash"] == generated["content_hash"]
    assert context["emitted_module_hash"] == generated["emitted_module_hash"]
    assert context["stack_manifest_hash"] == deployment.stack_manifest_hash

    drifted_artifact = dict(artifact)
    drifted_generated = dict(generated)
    drifted_generated.pop("content_hash")
    drifted_commits = dict(drifted_generated["producer_commits"])
    drifted_commits["pine2ast"] = "9" * 40
    drifted_generated["producer_commits"] = drifted_commits
    drifted_artifact["generated_artifact"] = seal_content_hash(
        drifted_generated,
        schema_id="openpine.generated_artifact.v2",
    )
    with pytest.raises(AdmitError, match="producer commit drift"):
        execution_context_from_admission(
            deployment,
            manifest,
            run_id="run-drift",
            strategy_id="strategy-1",
            artifact=drifted_artifact,
            data_snapshot_hash=HASH_D,
            series_id="binance/spot/BTCUSDT:1m",
            instrument_id="binance/spot/BTCUSDT",
            exchange="binance",
            market="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            semantic_profile="strict_5x",
            created_at_utc_ms=1,
        )


def _bar(close: float = 1.0, *, time: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        time=time,
        time_close=time + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=close,
        volume=10.0,
    )


def _sealed_artifact() -> dict[str, object]:
    python_code = "class GeneratedStrategy:\n    pass\n"
    return make_sealed_artifact(python_code=python_code)


def test_execution_data_snapshot_hash_preserves_exact_float_and_order() -> None:
    from openpine.run_identity import execution_data_snapshot_hash

    common = {
        "exchange": "binance",
        "market": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "start_ms": 0,
        "end_ms": 120_000,
        "finality_policy": "CLOSED_BAR_ONLY",
    }
    first = _bar(1.0000000000000002, time=0)
    second = _bar(1.0000000000000004, time=60_000)

    baseline = execution_data_snapshot_hash(bars=[first, second], **common)
    narrowed_difference = execution_data_snapshot_hash(
        bars=[_bar(1.0000000000000004, time=0), second], **common
    )
    reversed_hash = execution_data_snapshot_hash(bars=[second, first], **common)
    supplemental = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_400_000,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        }
    ]
    with_supplemental = execution_data_snapshot_hash(
        bars=[first, second], supplemental_bars=supplemental, **common
    )
    changed_supplemental = [dict(supplemental[0], close=1.6)]
    with_changed_supplemental = execution_data_snapshot_hash(
        bars=[first, second],
        supplemental_bars=changed_supplemental,
        **common,
    )

    assert baseline.startswith("sha256:")
    assert baseline != narrowed_difference
    assert baseline != reversed_hash
    assert with_supplemental != with_changed_supplemental


def test_generated_artifact_hash_requires_verified_sealed_identity() -> None:
    from openpine.run_identity import generated_artifact_hash

    artifact = _sealed_artifact()
    expected = artifact["generated_artifact"]["content_hash"]  # type: ignore[index]
    assert generated_artifact_hash(artifact) == expected

    with pytest.raises(AdmitError, match="sealed generated artifact"):
        generated_artifact_hash({"generated_artifact": None})

    tampered = _sealed_artifact()
    tampered["generated_artifact"]["source_hash"] = HASH_D  # type: ignore[index]
    with pytest.raises(AdmitError, match="content hash"):
        generated_artifact_hash(tampered)

    schema_invalid = {
        "generated_artifact": seal_content_hash(
            {"schema_id": "openpine.generated_artifact.v2", "source_hash": HASH_A},
            schema_id="openpine.generated_artifact.v2",
        )
    }
    with pytest.raises(AdmitError, match="schema"):
        generated_artifact_hash(schema_invalid)


def test_verified_generated_source_is_bound_to_the_same_artifact_envelope() -> None:
    from openpine.run_identity import verified_generated_source

    artifact = _sealed_artifact()
    assert verified_generated_source(artifact) == str(artifact["python_code"]).encode()

    tampered = _sealed_artifact()
    tampered["python_code"] = str(tampered["python_code"]) + "# drift\n"
    with pytest.raises(AdmitError, match="emitted module hash"):
        verified_generated_source(tampered)


def test_mutating_routes_do_not_recapture_artifact_after_identity_load() -> None:
    from openpine.gateway.routes import backtest, optimizer, tv_parity

    assert "capture_generated_source" not in inspect.getsource(
        backtest._run_backtest_background
    )
    assert "capture_generated_source" not in inspect.getsource(optimizer.optimizer_search)
    assert "capture_generated_source" not in inspect.getsource(
        tv_parity._run_tv_parity_background
    )


def test_run_identity_is_admitted_sealed_and_persisted_before_execution(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openpine.build_identity import BuildIdentity
    from openpine.run_identity import admit_and_persist_run_identity, run_identity_path

    monkeypatch.setattr(
        "openpine.run_identity.current_build_identity",
        lambda: BuildIdentity(version="5.0.0rc4", commit="1" * 40),
    )
    bars = [_bar(time=0), _bar(time=60_000)]
    artifact = _sealed_artifact()

    payload = admit_and_persist_run_identity(
        data_dir=tmp_path,
        deployment=_deployment(),
        admitted_manifest={
            "manifest_hash": HASH_A,
            "components": {"openpine": {"sha": "1" * 40}},
        },
        mode="backtest",
        run_id="bt_123",
        artifact=artifact,
        bars=bars,
        exchange="binance",
        market="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_ms=0,
        end_ms=120_000,
        semantic_profile="strict_5x",
        finality_policy="CLOSED_BAR_ONLY",
        warmup_policy="CALC_ONLY",
        score_policy="ALL_BARS",
        required_capabilities=("closed_bar", "deterministic_clock"),
        created_at_utc_ms=123,
    )

    validate_payload("openpine.run.v2", payload)
    assert verify_content_hash(payload, schema_id="openpine.run.v2")
    assert payload["state"] == "ADMITTED"
    assert payload["generated_artifact_hash"] == artifact["generated_artifact"]["content_hash"]  # type: ignore[index]
    assert payload["data_snapshot_hash"].startswith("sha256:")

    path = run_identity_path(tmp_path, "bt_123")
    assert json.loads(path.read_text(encoding="utf-8")) == payload

    monkeypatch.setattr(
        "openpine.run_identity.current_build_identity",
        lambda: BuildIdentity(version="5.0.0rc4", commit="2" * 40),
    )
    with pytest.raises(AdmitError, match="producer commit"):
        admit_and_persist_run_identity(
            data_dir=tmp_path,
            deployment=_deployment(),
            admitted_manifest={
                "manifest_hash": HASH_A,
                "components": {"openpine": {"sha": "1" * 40}},
            },
            mode="backtest",
            run_id="bt_drift",
            artifact=artifact,
            bars=bars,
            exchange="binance",
            market="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            start_ms=0,
            end_ms=120_000,
            semantic_profile="strict_5x",
            finality_policy="CLOSED_BAR_ONLY",
            warmup_policy="CALC_ONLY",
            score_policy="ALL_BARS",
            required_capabilities=("closed_bar", "deterministic_clock"),
            created_at_utc_ms=123,
        )


def test_run_identity_persistence_rejects_path_escape_and_conflicting_replay(tmp_path) -> None:
    from openpine.run_identity import persist_run_identity

    payload = {"content_hash": HASH_A, "run_id": "safe"}
    with pytest.raises(AdmitError, match="run_id"):
        persist_run_identity(tmp_path, "../escape", payload)

    persist_run_identity(tmp_path, "safe", payload)
    with pytest.raises(AdmitError, match="conflicting"):
        persist_run_identity(
            tmp_path,
            "safe",
            {"content_hash": HASH_B, "run_id": "safe"},
        )


def test_run_identity_rejects_symlinked_directory_and_target(tmp_path) -> None:
    from openpine.run_identity import persist_run_identity

    outside = tmp_path / "outside"
    outside.mkdir()
    identity_dir = tmp_path / "run-identities"
    identity_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AdmitError, match="symlink"):
        persist_run_identity(
            tmp_path,
            "escaped",
            {"content_hash": HASH_A, "run_id": "escaped"},
        )
    assert not (outside / "escaped.json").exists()

    identity_dir.unlink()
    identity_dir.mkdir()
    outside_target = outside / "target.json"
    outside_target.write_text("{}", encoding="utf-8")
    (identity_dir / "target.json").symlink_to(outside_target)
    with pytest.raises(AdmitError, match="symlink"):
        persist_run_identity(
            tmp_path,
            "target",
            {"content_hash": HASH_A, "run_id": "target"},
        )
    assert outside_target.read_text(encoding="utf-8") == "{}"


def test_run_identity_concurrent_conflict_has_exactly_one_winner(tmp_path) -> None:
    import openpine.run_identity as run_identity
    payloads = [
        {"content_hash": HASH_A, "run_id": "race", "tag": "A"},
        {"content_hash": HASH_B, "run_id": "race", "tag": "B"},
    ]
    outcomes: list[str] = []

    def publish(payload: dict[str, object]) -> None:
        try:
            run_identity.persist_run_identity(tmp_path, "race", payload)
        except AdmitError:
            outcomes.append("conflict")
        else:
            outcomes.append("ok")

    threads = [threading.Thread(target=publish, args=(payload,)) for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["conflict", "ok"]
    persisted = json.loads(
        run_identity.run_identity_path(tmp_path, "race").read_text(encoding="utf-8")
    )
    assert persisted in payloads


def test_background_backtest_admits_loaded_bytes_before_worker_dispatch() -> None:
    from openpine.gateway.routes import backtest

    source = inspect.getsource(backtest._run_backtest_background)
    admission = source.index("_admit_loaded_backtest_run(")
    dispatch = source.index("_run_owned_backtest(")
    assert admission < dispatch


def test_spawned_backtest_rehashes_exact_bars_before_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from openpine.gateway.routes import backtest

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        backtest,
        "_put_backtest_process_error",
        lambda _out, exc: seen.setdefault("error", exc),
    )
    monkeypatch.setattr(
        backtest,
        "_put_backtest_process_result",
        lambda _out, result: seen.setdefault("result", result),
    )
    spec = backtest._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir="/tmp/cache",
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
        source=b"class Generated: pass\n",
        data_snapshot_hash=HASH_A,
        execution_context={},
        admitted_manifest={},
        generated_artifact={},
        run_hash=HASH_B,
        bar_envelopes=[],
        protocol_artifact_dir="/tmp/protocol",
    )
    config = SimpleNamespace(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        finality_policy="CLOSED_BAR_ONLY",
    )

    backtest._artifact_backtest_process_entry(object(), spec, [_bar()], config, {})

    assert "result" not in seen
    assert "data snapshot hash mismatch" in str(seen["error"])


def test_optimizer_search_persists_run_identity_before_external_execution() -> None:
    from openpine.gateway.routes import optimizer

    source = inspect.getsource(optimizer.optimizer_search)
    admission = source.index("admit_and_persist_run_identity(")
    dispatch = source.index("start_optimization")
    assert admission < dispatch


def test_bind_isolated_execution_attaches_exact_protocol_inputs(tmp_path) -> None:
    from openpine.run_identity import bind_isolated_execution, run_identity_path
    from tests.admission_helpers import make_sealed_artifact
    from tests.rc4_fixtures import (
        admitted_manifest,
        canonical_bar_envelopes,
        execution_context,
    )

    bar = _bar()
    config = SimpleNamespace(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        semantic_profile="strict_5x",
    )
    manifest = admitted_manifest()
    schema_ids = (
        "openpine.execution_context.v1",
        "openpine.intent.v2",
        "openpine.worker.protocol.v2",
        "openpine.checkpoint.v1",
        "openpine.checkpoint.proof.v1",
    )
    deployment = DeploymentAdmissionIdentity(
        stack_id="5.0.0-rc.4",
        stack_manifest_hash=str(manifest["manifest_hash"]),
        wheel_identities=tuple(
            (name, "5.0.0rc4", HASH_B) for name in STACK_COMPONENTS
        ),
        schema_hashes={name: HASH_C for name in schema_ids},
        capabilities=frozenset(
            {
                "closed_bar",
                "deterministic_clock",
                "isolated_worker",
                "broker_projection",
                "intent_tape_v2",
            }
        ),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )
    artifact = make_sealed_artifact(python_code="VALUE = 1\n")
    envelopes = canonical_bar_envelopes([bar], execution_context())

    identity = bind_isolated_execution(
        config,
        data_dir=tmp_path,
        deployment=deployment,
        admitted_manifest=manifest,
        mode="backtest",
        run_id="run-bind",
        strategy_id="strategy-test",
        artifact=artifact,
        bars=[bar],
        bar_envelopes=envelopes,
        supplemental_bars=None,
        created_at_utc_ms=1,
    )

    assert config.execution_context["run_id"] == "run-bind"
    assert config.generated_artifact == artifact["generated_artifact"]
    assert config.bar_envelopes == envelopes
    assert config.run_hash == identity["content_hash"]
    assert config.protocol_artifact_dir.endswith("protocol/run-bind")
    assert run_identity_path(tmp_path, "run-bind").is_file()


def test_optimizer_runner_rehashes_bars_before_each_trial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.optimizer.isolated_runner import IsolatedOptimizerRunner

    called = False

    class Adapter:
        def run_isolated(self, *_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("engine must not run after data identity drift")

    monkeypatch.setattr(
        "openpine.optimizer.isolated_runner.BacktestEngineAdapter", Adapter
    )
    config = SimpleNamespace(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_400_000,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        }
    ]
    from openpine.run_identity import execution_data_snapshot_hash

    expected = execution_data_snapshot_hash(
        bars=[_bar()],
        supplemental_bars=htf_bars,
        exchange="binance",
        market="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_ms=0,
        end_ms=60_000,
        finality_policy="CLOSED_BAR_ONLY",
    )
    from tests.admission_helpers import make_sealed_artifact
    from tests.rc4_fixtures import HASH_A, admitted_manifest, execution_context

    generated_artifact = make_sealed_artifact(python_code="source")[
        "generated_artifact"
    ]
    runner = IsolatedOptimizerRunner(
        source=b"source",
        bars=[_bar()],
        config=config,
        expected_data_snapshot_hash=expected,
        execution_context=execution_context(),
        admitted_manifest=admitted_manifest(),
        instrument_id="test:BTCUSDT",
        generated_artifact=generated_artifact,
        bar_envelopes=[],
        run_hash=HASH_A,
        protocol_artifact_dir="/tmp/openpine-test-protocol-artifacts",
        htf_bars=htf_bars,
    )
    runner.htf_bars[0]["close"] = 1.6
    request = SimpleNamespace(params={}, required_metrics=(), fingerprints={})

    with pytest.raises(RuntimeError, match="data snapshot hash mismatch"):
        runner(request)
    assert called is False


def test_tv_parity_persists_run_identity_before_replay_dispatch() -> None:
    from openpine.gateway.routes import tv_parity

    source = inspect.getsource(tv_parity._run_tv_parity_background)
    admission = source.index("admit_and_persist_run_identity(")
    dispatch = source.index("_run_isolated_tv_replay")
    assert admission < dispatch


def test_tv_replay_rehashes_bars_before_isolated_worker() -> None:
    from openpine.gateway.routes.tv_parity import _run_isolated_tv_replay

    called = False

    class Adapter:
        def run_isolated(self, *_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("worker must not run after data identity drift")

    config = SimpleNamespace(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_400_000,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        }
    ]
    from openpine.run_identity import execution_data_snapshot_hash

    expected = execution_data_snapshot_hash(
        bars=[_bar()],
        supplemental_bars=htf_bars,
        exchange="binance",
        market="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        start_ms=0,
        end_ms=60_000,
        finality_policy="CLOSED_BAR_ONLY",
    )
    htf_bars[0]["close"] = 1.6
    with pytest.raises(RuntimeError, match="data snapshot hash mismatch"):
        _run_isolated_tv_replay(
            Adapter(),
            b"source",
            [_bar()],
            config,
            {},
            None,
            htf_bars=htf_bars,
            expected_data_snapshot_hash=expected,
        )
    assert called is False
