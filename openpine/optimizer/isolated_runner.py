"""External optimizer runner backed by OpenPine's isolated artifact path."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from typing import Any

from optimizer import RunnerCapabilities, RunnerResponse

from openpine.runtime.engine import BacktestEngineAdapter


def _identity(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _identity(asdict(value))
    if isinstance(value, dict):
        return {str(key): _identity(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_identity(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    payload: dict[str, Any] = {}
    for name in ("time", "time_close", "open", "high", "low", "close", "volume"):
        if hasattr(value, name):
            payload[name] = getattr(value, name)
    return payload or {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        _identity(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        base_params: dict[str, Any] | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
    ) -> None:
        self.source = bytes(source)
        self.bars = tuple(bars)
        self.config = config
        self.base_params = dict(base_params or {})
        self.htf_bars = list(htf_bars or [])
        self.data_fingerprint = _stable_hash(self.bars)
        self.engine_config_hash = _stable_hash(config)
        self.runner_fingerprint = _stable_hash(
            {
                "contract": "openpine.isolated_optimizer_runner.v1",
                "source_sha256": hashlib.sha256(self.source).hexdigest(),
                "base_params": self.base_params,
            }
        )

    def fingerprint(self) -> str:
        return self.runner_fingerprint

    def __call__(self, request: Any) -> RunnerResponse:
        trial_params = {**self.base_params, **dict(request.params)}
        isolated = BacktestEngineAdapter().run_isolated(
            self.source,
            list(self.bars),
            self.config,
            htf_bars=self.htf_bars or None,
            params=trial_params,
        )
        result = isolated.raw_result
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
            except (TypeError, ValueError):
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
        if getattr(result, "status", "completed") != "completed":
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
            "engine_config_hash": self.engine_config_hash,
            "score_ledger_hash": str(getattr(result, "score_ledger_hash", None) or ""),
        }
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
