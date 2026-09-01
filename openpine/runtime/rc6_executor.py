"""Native RC6 GeneratedScript transaction executor."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from openpine.run_identity import generated_artifact_hash, verified_generated_source
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession
from pinelib.runtime.delegated import DelegatedCapabilityDispatcher
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
from pinelib.runtime.session import CallbackResult


class RC6RuntimeExecutor:
    """Execute one V3 generated module against a persistent PineLib session."""

    def __init__(
        self,
        *,
        artifact: Mapping[str, object],
        language: RuntimeLanguageContext,
        instrument: InstrumentContext,
        timeframe: TimeframeContext,
        delegated_dispatcher: DelegatedCapabilityDispatcher | None = None,
    ) -> None:
        generated_artifact_hash(artifact)
        source = verified_generated_source(artifact).decode("utf-8")
        envelope = artifact.get("generated_artifact")
        if not isinstance(envelope, Mapping):
            raise ValueError("generated artifact envelope is required")
        entrypoint = envelope.get("entrypoint")
        if not isinstance(entrypoint, Mapping) or set(entrypoint) != {"module", "class"}:
            raise ValueError("generated artifact entrypoint is malformed")
        module_name = entrypoint.get("module")
        entrypoint_name = entrypoint.get("class")
        if not isinstance(module_name, str) or not isinstance(entrypoint_name, str):
            raise ValueError("generated artifact entrypoint identity is malformed")
        self._verify_import_manifest(source, envelope)

        namespace: dict[str, Any] = {"__name__": module_name}
        exec(compile(source, f"<{module_name}>", "exec"), namespace, namespace)
        generated_class = namespace.get(entrypoint_name)
        if not isinstance(generated_class, type):
            raise ValueError("generated artifact entrypoint class is unavailable")
        if tuple(inspect.signature(generated_class).parameters) != ("runtime",):
            raise ValueError("GeneratedScript constructor must accept exactly runtime")
        run_method = getattr(generated_class, "run", None)
        if not callable(run_method) or tuple(inspect.signature(run_method).parameters) != ("self",):
            raise ValueError("GeneratedScript.run must accept only self")

        self.module_name = module_name
        self.entrypoint_name = entrypoint_name
        self.generated_class = generated_class
        self.namespace = MappingProxyType(namespace)
        self.session = RuntimeSession(
            language,
            instrument=instrument,
            timeframe=timeframe,
            delegated_dispatcher=delegated_dispatcher,
        )

    @staticmethod
    def _verify_import_manifest(source: str, envelope: Mapping[str, object]) -> None:
        imports: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        expected = envelope.get("import_manifest")
        if not isinstance(expected, list) or sorted(imports) != expected:
            raise ValueError("generated module imports differ from sealed import manifest")

    def execute_bar(
        self,
        values: BarValues,
        *,
        bar_index: int,
        last_bar_index: int,
        realtime: bool = False,
        tick_index: int = 0,
        final_tick: bool = True,
    ) -> CallbackResult:
        sequence = self.session.sequence + 1
        frame = CallbackFrame(
            "REALTIME_EVAL" if realtime else "HISTORICAL_EVAL",
            sequence,
            realtime=realtime,
            final_tick=final_tick,
            bar_index=bar_index,
            tick_index=tick_index,
            is_last_bar=bar_index == last_bar_index,
            is_last_confirmed_history=(not realtime and bar_index == last_bar_index),
            last_bar_index=last_bar_index,
        )
        transaction = self.session.begin(frame, values=values)
        try:
            instance = self.generated_class(transaction)
            instance.run()
            return transaction.commit()
        except Exception:
            if not transaction.closed:
                transaction.abort()
            raise


__all__ = ["RC6RuntimeExecutor"]
