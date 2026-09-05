"""Resolve only hash-bound generated input descriptors, before worker startup."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from pinelib.errors import PineRuntimeError
from pinelib.input import InputRegistry


class InputBindingError(ValueError):
    code = "RC6_INPUT_INVALID"


def read_input_descriptors(
    source: str | bytes,
    *,
    envelope: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Read a compiler-emitted literal without executing the generated module.

    Descriptor identity is covered by the emitted-module hash; callers still
    perform normal artifact admission. Older input-free artifacts have no metadata constant.
    """
    try:
        tree = ast.parse(source)
        nodes = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SCRIPT_METADATA"
                for target in node.targets
            )
        ]
        if not nodes:
            return ()
        if len(nodes) != 1:
            raise InputBindingError("duplicate generated input descriptors")
        raw = ast.literal_eval(nodes[0])
        if (
            not isinstance(raw, dict)
            or raw.get("schema_id") != "ast2python.script_metadata.v1"
            or not isinstance(raw.get("inputs"), dict)
        ):
            raise InputBindingError("generated script metadata is malformed")
        if envelope is not None:
            from ast2python.artifacts.script_metadata import admitted_script_metadata

            admitted_script_metadata({"SCRIPT_METADATA": raw}, envelope)
        rows = raw["inputs"]
        if any(
            not isinstance(row, dict) or row.get("input_id") != key for key, row in rows.items()
        ):
            raise InputBindingError("generated input descriptor identity is malformed")
        return tuple(dict(row) for row in rows.values())
    except (SyntaxError, UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise InputBindingError(f"invalid generated input descriptors: {exc}") from exc


def resolve_inputs(
    source: str | bytes,
    params: Mapping[str, object] | None = None,
    *,
    envelope: Mapping[str, Any] | None = None,
) -> InputRegistry:
    try:
        return InputRegistry.from_descriptors(
            {row["input_id"]: row for row in read_input_descriptors(source, envelope=envelope)},
            params,
        )
    except (PineRuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise InputBindingError(str(exc)) from exc


def input_evidence(registry: InputRegistry) -> dict[str, Any]:
    return {
        "input_values_hash": registry.values_hash,
        "input_registry_hash": registry.identity_hash,
        "effective_inputs": dict(registry.values),
    }


def applied_config_hash(config: Any, registry: InputRegistry) -> str:
    """Bind the engine settings to the values actually installed in PineLib."""
    from openpine.runtime.rc6_config import serialize_engine_config
    from openpine_contracts import content_hash

    from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig

    if isinstance(config, BacktestRunConfig):
        config = BacktestEngineAdapter()._to_engine_config(config)
    settings = serialize_engine_config(config, str(config.semantic_profile))
    return content_hash(
        {
            "schema_id": "openpine.rc6.applied_config.v1",
            "engine_config_hash": settings["effective_config_hash"],
            "input_values_hash": registry.values_hash,
        },
        schema_id="openpine.rc6.applied_config.v1",
    )
