"""External optimizer runner backed by OpenPine's isolated artifact path."""

from __future__ import annotations

import hashlib
import json
import math
import copy
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from optimizer import RunnerCapabilities, RunnerResponse

from openpine.runtime.engine import BacktestEngineAdapter


def _identity(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _identity(asdict(value))
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise TypeError("optimizer identity mapping keys must be strings")
        return {key: _identity(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_identity(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    payload: dict[str, Any] = {}
    for name in ("time", "time_close", "open", "high", "low", "close", "volume"):
        if hasattr(value, name):
            payload[name] = getattr(value, name)
    if not payload:
        raise TypeError(f"unsupported optimizer identity value: {type(value).__name__}")
    return payload


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        _identity(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _engine_metric_requirements(names: set[str]) -> set[str]:
    """Translate result-field requirements into the broker's calculation switches.

    Ordinary numeric fields are always produced. The broker's required_metrics
    currently accepts special computations (sharpe/sortino), not field names.
    Returned values are still checked for presence and finiteness after execution.
    """
    from typing import get_type_hints
    from backtest_engine.results.result import BacktestResult

    types = get_type_hints(BacktestResult)
    unknown = {name for name in names if types.get(name) not in (int, float, float | None)}
    if unknown:
        raise ValueError(f"unsupported optimizer result metrics: {sorted(unknown)}")
    switches = {
        "sharpe_ratio": "sharpe", "sortino_ratio": "sortino",
        "score_sharpe_ratio": "sharpe", "score_sortino_ratio": "sortino",
    }
    return {switches[name] for name in names if name in switches}


class IsolatedOptimizerRunner:
    """Run each optimizer trial through captured bytes and Bubblewrap."""

    capabilities = RunnerCapabilities(
        supports_runner_request=True,
        supports_required_outputs=True,
        supported_outputs={"summary_metrics", "closed_trades", "equity_curve"},
        supports_content_hash=True,
        supports_data_fingerprint=True,
        supports_engine_config_hash=True,
    )

    def __init__(
        self,
        *,
        source: bytes,
        bars: tuple[Any, ...] | list[Any],
        config: Any,
        expected_data_snapshot_hash: str,
        execution_context: dict[str, Any],
        admitted_manifest: dict[str, Any],
        instrument_id: str,
        generated_artifact: dict[str, Any],
        bar_envelopes: list[dict[str, Any]],
        run_identity: dict[str, Any],
        data_dir: str | Path,
        protocol_artifact_dir: str,
        base_params: dict[str, Any] | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
    ) -> None:
        self.source = bytes(source)
        self.bars = copy.deepcopy(tuple(bars))
        self.config = copy.deepcopy(config)
        self.execution_context = copy.deepcopy(execution_context)
        self.admitted_manifest = copy.deepcopy(admitted_manifest)
        self.instrument_id = instrument_id
        self.generated_artifact = copy.deepcopy(generated_artifact)
        self.bar_envelopes = copy.deepcopy(bar_envelopes)
        self._envelope_hash = _stable_hash(self.bar_envelopes)
        self.run_identity = copy.deepcopy(run_identity)
        self.data_dir = Path(data_dir)
        self.protocol_artifact_dir = Path(protocol_artifact_dir)
        self.expected_data_snapshot_hash = expected_data_snapshot_hash
        self.base_params = copy.deepcopy(dict(base_params or {}))
        self.htf_bars = copy.deepcopy(list(htf_bars or []))
        self.data_fingerprint = _stable_hash(self.bars)
        from openpine.runtime.inputs import applied_config_hash, resolve_inputs

        inputs = resolve_inputs(self.source, self.base_params, envelope=self.generated_artifact)
        self.engine_config_hash = applied_config_hash(self.config, inputs)
        self.runner_fingerprint = _stable_hash(
            {
                "contract": "openpine.isolated_optimizer_runner.v1",
                "source_sha256": hashlib.sha256(self.source).hexdigest(),
                "generated_artifact_hash": self.generated_artifact["content_hash"],
                "applied_config_hash": self.engine_config_hash,
                "data_snapshot_hash": self.expected_data_snapshot_hash,
                "canonical_envelopes_hash": self._envelope_hash,
            }
        )

    def fingerprint(self) -> str:
        return self.runner_fingerprint

    def __call__(self, request: Any) -> RunnerResponse:
        from openpine.run_identity import (
            execution_data_snapshot_hash,
            persist_run_identity,
        )
        from openpine_contracts import seal_content_hash, validate_payload

        actual_snapshot_hash = execution_data_snapshot_hash(
            bars=self.bars,
            supplemental_bars=self.htf_bars,
            exchange=str(self.config.exchange),
            market=str(self.config.market_type),
            symbol=str(self.config.symbol),
            timeframe=str(self.config.timeframe),
            start_ms=int(self.config.start_time),
            end_ms=int(self.config.end_time),
            finality_policy="CLOSED_BAR_ONLY",
        )
        if actual_snapshot_hash != self.expected_data_snapshot_hash:
            raise RuntimeError("data snapshot hash mismatch before optimizer trial")
        if _stable_hash(self.bar_envelopes) != self._envelope_hash:
            raise RuntimeError("canonical envelope identity drift before optimizer trial")
        if (
            isinstance(request.trial_id, bool)
            or not isinstance(request.trial_id, int)
            or request.trial_id < 0
        ):
            raise RuntimeError("optimizer trial_id must be an integer")
        from optimizer.core.contracts import RUNNER_CONTRACT
        from openpine.runtime.inputs import applied_config_hash, resolve_inputs

        if getattr(request, "contract", RUNNER_CONTRACT) != RUNNER_CONTRACT:
            raise ValueError("unsupported optimizer runner contract")
        for field in ("range", "seed"):
            if getattr(request, field, None) is not None:
                raise ValueError(f"isolated optimizer does not support {field}")
        if getattr(request, "early_stop_conditions", None):
            raise ValueError("isolated optimizer does not support early_stop_conditions")
        outputs = set(getattr(request, "required_outputs", ()))
        if outputs - self.capabilities.supported_outputs:
            raise ValueError("unsupported required optimizer outputs")
        metric_switches = _engine_metric_requirements(set(request.required_metrics))
        trial_params = {**self.base_params, **dict(request.params)}
        inputs = resolve_inputs(self.source, trial_params, envelope=self.generated_artifact)
        trial_params = dict(inputs.values)
        trial_config = copy.deepcopy(self.config)
        object.__setattr__(trial_config, "required_outputs", outputs)
        object.__setattr__(trial_config, "required_metrics", metric_switches)
        trial_run_id = f"{self.execution_context['run_id']}.trial-{request.trial_id}"
        trial_context_payload = dict(self.execution_context)
        trial_context_payload.pop("content_hash", None)
        trial_context_payload["run_id"] = trial_run_id
        trial_context_payload["session_id"] = f"{trial_run_id}.session"
        trial_context = seal_content_hash(
            trial_context_payload, schema_id="openpine.execution_context.v1"
        )
        validate_payload("openpine.execution_context.v1", trial_context)
        manifest = getattr(self.config, "request_manifest", None)
        if manifest is not None:
            from openpine.runtime.request_data import rebind_request_manifest

            # Reseal only the execution binding, never replace or reinterpret data.
            manifest = rebind_request_manifest(manifest, self.execution_context, trial_context)
            object.__setattr__(trial_config, "request_manifest", manifest)
        config_hash = applied_config_hash(trial_config, inputs)
        object.__setattr__(trial_config, "applied_config_hash", config_hash)
        trial_run_payload = dict(self.run_identity)
        trial_run_payload.pop("content_hash", None)
        trial_run_payload["run_id"] = trial_run_id
        trial_run_payload["config_hash"] = config_hash
        trial_run = seal_content_hash(trial_run_payload, schema_id="openpine.run.v2")
        validate_payload("openpine.run.v2", trial_run)
        persist_run_identity(self.data_dir, trial_run_id, trial_run)

        object.__setattr__(trial_config, "execution_context", trial_context)
        object.__setattr__(trial_config, "admitted_manifest", self.admitted_manifest)
        object.__setattr__(trial_config, "instrument_id", self.instrument_id)
        object.__setattr__(trial_config, "generated_artifact", self.generated_artifact)
        object.__setattr__(trial_config, "bar_envelopes", self.bar_envelopes)
        object.__setattr__(trial_config, "run_hash", trial_run["content_hash"])
        object.__setattr__(
            trial_config,
            "protocol_artifact_dir",
            str(self.protocol_artifact_dir / f"trial-{request.trial_id}"),
        )
        isolated = BacktestEngineAdapter().run_isolated(
            self.source,
            copy.deepcopy(list(self.bars)),
            trial_config,
            htf_bars=copy.deepcopy(self.htf_bars) or None,
            params=trial_params,
        )
        result = isolated.raw_result
        if getattr(result, "input_values_hash", None) != inputs.values_hash:
            raise RuntimeError("optimizer result does not attest the applied input values")
        metrics: dict[str, float] = {}
        diagnostics: list[dict[str, Any]] = []
        for name in set(request.required_metrics):
            value = getattr(result, name, None)
            if value is None or isinstance(value, bool):
                diagnostics.append(
                    {
                        "code": "ISOLATED_OPTIMIZER_METRIC_MISSING",
                        "message": f"required metric {name!r} is unavailable",
                        "severity": "error",
                        "context": {"metric": name},
                    }
                )
                continue
            try:
                metric = float(value)
            except (TypeError, ValueError, OverflowError):
                metric = math.nan
            if not math.isfinite(metric):
                diagnostics.append(
                    {
                        "code": "ISOLATED_OPTIMIZER_METRIC_INVALID",
                        "message": f"required metric {name!r} is not finite",
                        "severity": "error",
                        "context": {"metric": name},
                    }
                )
                continue
            metrics[name] = metric
        if getattr(result, "status", None) != "completed":
            diagnostics.append(
                {
                    "code": "ISOLATED_OPTIMIZER_BACKTEST_FAILED",
                    "message": f"isolated backtest returned {getattr(result, 'status', None)!r}",
                    "severity": "error",
                }
            )
        content_hash = getattr(result, "content_hash", None)
        hashes = {
            **dict(request.fingerprints),
            "data_fingerprint": str(
                getattr(result, "data_fingerprint", None) or self.data_fingerprint
            ),
            "runner_fingerprint": self.runner_fingerprint,
            "engine_config_hash": config_hash,
            "input_values_hash": inputs.values_hash,
            "run_identity_hash": trial_run["content_hash"],
            "score_ledger_hash": str(getattr(result, "score_ledger_hash", None) or ""),
        }
        if manifest is not None:
            hashes["source_request_manifest_hash"] = self.config.request_manifest["content_hash"]
            hashes["trial_request_manifest_hash"] = manifest["content_hash"]
        if callable(content_hash):
            hashes["content_hash"] = str(content_hash())
        return RunnerResponse(
            metrics=metrics,
            raw_result=result,
            hashes=hashes,
            trades_available=getattr(result, "closed_trades", None) is not None,
            equity_available=getattr(result, "equity_curve", None) is not None,
            diagnostics=diagnostics,
        )


__all__ = ["IsolatedOptimizerRunner"]
