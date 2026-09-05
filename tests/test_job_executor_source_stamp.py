from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe
from openpine_contracts import seal_content_hash, validate_payload, verify_content_hash

from openpine.registry.strategies import StrategyInstance
from openpine.workers.strategy_job_executor import StrategyJobExecutor

_COMPONENTS = (
    "openpine-contracts",
    "pine2ast",
    "ast2python",
    "pinelib",
    "marketdata-provider",
    "backtest_engine",
    "optimizer",
    "openpine",
)


def _sealed_execution_context(envelope: dict[str, str]) -> dict[str, object]:
    stack = "sha256:" + "d" * 64
    payload = {
        "schema_id": "openpine.execution_context.v1",
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": "5.0.0",
        "producer_commit": "8" * 40,
        "stack_id": stack,
        "created_at_utc_ms": 0,
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "run_id": "run-rc6",
        "strategy_id": "strategy-1",
        "session_id": "run-rc6:worker",
        "stack_manifest_hash": stack,
        "wheel_identities": [
            {"name": name, "version": "5.0.0rc6", "content_hash": "sha256:" + f"{index:x}" * 64}
            for index, name in enumerate(_COMPONENTS, start=1)
        ],
        "schema_hashes": {
            schema_id: "sha256:" + "e" * 64
            for schema_id in (
                "openpine.execution_context.v1",
                "openpine.intent.v2",
                "openpine.worker.protocol.v2",
                "openpine.checkpoint.v1",
                "openpine.checkpoint.proof.v1",
            )
        },
        "generated_artifact_hash": envelope["content_hash"],
        "source_hash": envelope["source_hash"],
        "emitted_module_hash": envelope["emitted_module_hash"],
        "data_snapshot_hash": "sha256:" + "f" * 64,
        "series_id": "binance:spot:BTCUSDT:15m",
        "instrument_id": "binance:spot:BTCUSDT",
        "exchange": "binance",
        "market": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "timezone": "UTC",
        "currency": "USD",
        "mintick": "0.01",
        "pointvalue": "1",
        "session_policy": "24x7",
        "semantic_profile": "strict_5x",
        "finality_policy": "CLOSED_BAR_ONLY",
        "warmup_policy": "CALC_ONLY",
        "score_policy": "ALL_BARS",
        "end_policy": "LIQUIDATE_ON_LAST_BAR",
        "capabilities": ["checkpoint_v1", "closed_bar", "deterministic_clock", "sealed_artifact_refs"],
        "producer_commits": {name: f"{index:x}" * 40 for index, name in enumerate(_COMPONENTS, start=1)},
        "policy_registry_version": "openpine.policies.rc4.v1",
        "schema_registry_version": "openpine.schemas.rc4.v1",
        "capability_registry_version": "openpine.capabilities.rc4.v1",
    }
    sealed = seal_content_hash(payload, schema_id="openpine.execution_context.v1")
    validate_payload("openpine.execution_context.v1", sealed)
    assert verify_content_hash(sealed, schema_id="openpine.execution_context.v1")
    return sealed


def _strategy() -> StrategyInstance:
    strategy = StrategyInstance(
        strategy_id="strategy-1",
        name="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_json="{}",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode="paper",
        enabled=True,
    )
    strategy.semantic_profile = "strict_5x"
    return strategy


def _bar(open_time: int = 0) -> Bar:
    tf = parse_timeframe("15m")
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=tf,
        time=open_time,
        time_close=open_time + (tf.duration_ms or 0),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=42.0,
        closed=True,
    )


class _Adapter:
    def __init__(self) -> None:
        self.sources: list[bytes] = []

    def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None, params=None):
        self.sources.append(source)
        return SimpleNamespace(ok=True)


def test_job_executor_stamps_source_once_across_bars(monkeypatch) -> None:
    captures: list[tuple] = []

    def capture(*args, **kwargs):
        captures.append((args, kwargs))
        return b"STAMPED"

    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        capture,
    )
    adapter = _Adapter()
    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=adapter,
    )
    strategy = _strategy()
    executor._run_strategy(strategy, _bar(0), None)
    executor._run_strategy(strategy, _bar(900_000), None)
    assert captures == [(("pine-1", "artifact-1"), {})]
    assert adapter.sources == [b"STAMPED", b"STAMPED"]


def test_job_executor_forwards_confirmed_htf_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None, params=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
        htf_bars=htf_bars,
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert seen["htf_bars"] == htf_bars


def test_job_executor_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None, params=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "time": 0,
            "time_close": 900_000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 42.0,
        }
    ]


def test_job_executor_does_not_invent_time_close(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None, params=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
    )
    bar = _bar(0)
    object.__setattr__(bar, "time_close", None)
    executor._run_strategy(_strategy(), bar, None)
    assert seen["htf_bars"] is None


def test_job_executor_fetches_explicit_htf_timeframe(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}
    loaded: list[str] = []
    fetched = [
        SimpleNamespace(
            time=0,
            time_close=86_399_999,
            open=40,
            high=43,
            low=39,
            close=42,
            volume=1,
        )
    ]

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None, params=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    def load_bars(query):
        loaded.append(str(getattr(query.timeframe, "canonical", query.timeframe)))
        return SimpleNamespace(bars=fetched)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(load_bars=load_bars),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
        htf_timeframe="1D",
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert "1D" in loaded
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_job_executor_same_htf_timeframe_does_not_refetch(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}
    loaded: list[object] = []

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None, params=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(load_bars=lambda query: loaded.append(query)),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
        htf_timeframe="15m",
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert loaded == []
    assert seen["htf_bars"][0]["timeframe"] == "15m"


def test_job_executor_passes_sealed_v3_envelope_to_run_isolated(monkeypatch) -> None:
    envelope = {
        "schema_id": "openpine.generated_artifact.v3",
        "content_hash": "sha256:" + "1" * 64,
        "source_hash": "sha256:" + "2" * 64,
        "emitted_module_hash": "sha256:" + "3" * 64,
        "producer": {"name": "ast2python", "commit": "b" * 40},
    }
    context = _sealed_execution_context(envelope)
    artifact = {
        "generated_artifact": envelope,
        "execution_context": context,
        "compile_meta": {"translation_metadata": {"declaration": {"arguments": {}}}},
    }
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )

    class _Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            assert artifact_id == "artifact-1"
            assert source_id == "pine-1"
            return artifact

    monkeypatch.setattr(
        "openpine.artifacts.ArtifactStore",
        lambda: _Store(),
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["source"] = source
            seen["generated_artifact"] = getattr(config, "generated_artifact", None)
            seen["execution_context"] = getattr(config, "execution_context", None)
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert seen["source"] == b"STAMPED"
    assert seen["generated_artifact"] == envelope
    passed = seen["execution_context"]
    assert isinstance(passed, dict)
    assert passed["schema_id"] == "openpine.execution_context.v1"
    assert passed["content_hash"]
    assert passed["generated_artifact_hash"] == envelope["content_hash"]
    assert passed["source_hash"] == envelope["source_hash"]
    assert passed["emitted_module_hash"] == envelope["emitted_module_hash"]
    assert passed == context
