"""Installed frontend -> exact target -> runtime -> host capability evidence.

BOUND is a checked binding, not numerical conformance. Unsupported and missing
rows remain visible. Dataset availability and oracle evidence are independent.
"""

from __future__ import annotations

from collections import Counter
from importlib import import_module
from typing import Any

from ast2python.lowering import load_pinelib_target_manifest
from pinelib.abi import load_target_manifest
from pine2ast.catalog import CatalogRepository
from openpine.runtime.strategy_host import strategy_host_surface
from openpine.runtime.worker_capabilities import WORKER_CAPABILITIES
from openpine.verification.identity import seal

MODES = ("interactive", "bulk_backtest")


def _callable(path: object) -> bool:
    if not isinstance(path, str) or not path.startswith("pinelib.") or "." not in path:
        return False
    module, name = path.rsplit(".", 1)
    try:
        return callable(getattr(import_module(module), name, None))
    except ImportError:
        return False


def build_capability_graph(mode: str = "interactive") -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("unknown execution mode")
    raw = load_target_manifest()
    target = load_pinelib_target_manifest()
    if target.release_acceptance != "EXACT_PINELIB_TARGET_MANIFEST_V2":
        raise ValueError("reference targets cannot enter the production capability graph")
    host = strategy_host_surface()
    names = set(host["commands"]) | set(host["constants"]) | set(host["state_values"])
    repo = CatalogRepository.default()
    catalogs = {v: repo.readonly_view(v) for v in range(1, 7)}
    rows, seen, covered = [], set(), set()
    projected_symbols = {key[0] for key in target.call_bindings}
    # Include every installed target row and all six version decisions. This is
    # the installed catalog denominator, not a claim of complete TV coverage.
    for row in raw["rows"]:
        for version, catalog in catalogs.items():
            category, name = row["category"], row["name"]
            key = (version, row["symbol_id"], row.get("overload_id"), category)
            if key in seen:
                raise ValueError("duplicate capability identity")
            seen.add(key)
            covered.add((version, row["symbol_id"], category))
            front = name in catalog.get(category, {})
            version_ok = version in row["version_availability"]
            disposition = row["disposition"]
            reasons = []
            if not front:
                reasons.append("FRONTEND_UNAVAILABLE")
            if not version_ok:
                reasons.append("VERSION_UNAVAILABLE")
            if disposition == "UNSUPPORTED_FAIL_CLOSED":
                reasons.append("TARGET_UNSUPPORTED")
            runtime = _callable(row["abi_callable"]) if disposition == "TARGET_DIRECT" else None
            if disposition == "TARGET_DIRECT" and not runtime:
                reasons.append("RUNTIME_CALLABLE_MISSING")
            delegation = row.get("delegation")
            handler = None
            if disposition == "TARGET_DELEGATED":
                handler = bool(
                    delegation
                    and delegation["owner"] == host["owner"]
                    and delegation["schema_id"] == host["delegation_schema_id"]
                    and delegation["capability_id"] in names
                )
                if not handler:
                    reasons.append("HOST_HANDLER_MISSING")
            binding_keys = {(row["symbol_id"], row["symbol_id"] + "#canonical", row["call_form"])}
            projected = (
                row["symbol_id"] in target.value_bindings
                if category in {"variables", "constants"}
                else any(k in target.call_bindings for k in binding_keys)
            )
            # Method and legacy spellings may have a different exact call form.
            if not projected and category not in {"variables", "constants"}:
                projected = row["symbol_id"] in projected_symbols
            if not projected:
                reasons.append("COMPILER_BINDING_MISSING")
            datasets = name.startswith("request.") or name == "security"
            rows.append(
                {
                    "pine_version": version,
                    "symbol_id": row["symbol_id"],
                    "overload_id": row.get("overload_id"),
                    "category": category,
                    "frontend": front,
                    "target": disposition,
                    "runtime_callable": runtime,
                    "host_handler": handler,
                    "dataset_requirement": "preload_required" if datasets else "none",
                    "oracle": "missing",
                    "status": "BOUND" if not reasons else "UNAVAILABLE",
                    "reasons": reasons,
                }
            )
    # Producer-only declarations/types/functions must not disappear from reports.
    for version, catalog in catalogs.items():
        for category in ("functions", "methods", "variables", "constants", "types", "declarations"):
            for name, info in catalog.get(category, {}).items():
                symbol = info.get("symbol_id", f"catalog:{category}:{name}")
                if (version, symbol, category) in covered:
                    continue
                rows.append(
                    {
                        "pine_version": version,
                        "symbol_id": symbol,
                        "overload_id": None,
                        "category": category,
                        "frontend": True,
                        "target": "NOT_MAPPED",
                        "runtime_callable": None,
                        "host_handler": None,
                        "dataset_requirement": "unknown",
                        "oracle": "missing",
                        "status": "UNVERIFIED",
                        "reasons": ["NO_TARGET_ROW"],
                    }
                )
    rows.sort(
        key=lambda r: (r["pine_version"], r["symbol_id"], r["overload_id"] or "", r["category"])
    )
    return seal(
        {
            "schema_id": "openpine.capability_graph.v1",
            "mode": mode,
            "catalogs": {str(v): repo.identity(v).catalog_hash for v in catalogs},
            "target_manifest_hash": target.content_hash,
            "runtime_manifest_hash": raw["content_hash"],
            "host_surface_hash": host["content_hash"],
            "constraints": host["constraints"],
            "protocol_capabilities": list(WORKER_CAPABILITIES),
            "rows": rows,
            "counts": dict(Counter(r["status"] for r in rows)),
            "denominator_kind": "installed_catalog_not_full_tradingview",
        }
    )


def effective_target_identity(
    pine_version: int, target_hash: str, mode: str = "interactive"
) -> dict:
    if type(pine_version) is not int or not 1 <= pine_version <= 6 or mode not in MODES:
        raise ValueError("invalid effective target context")
    return seal(
        {
            "schema_id": "openpine.effective_target.v1",
            "pine_version": pine_version,
            "catalog_hash": CatalogRepository.default().identity(pine_version).catalog_hash,
            "target_manifest_hash": target_hash,
            "host_surface_hash": strategy_host_surface()["content_hash"],
            "mode": mode,
            "protocol_capabilities": list(WORKER_CAPABILITIES),
            "oracle_status": "unverified",
        }
    )


def require_plan_bindings(plan, target) -> None:
    """Validate only callable bindings needed by this checked lowering plan."""
    if target.release_acceptance != "EXACT_PINELIB_TARGET_MANIFEST_V2":
        raise ValueError("reference target cannot be admitted as production")
    for opcode in plan.required_operations:
        operation = target.operations.get(opcode)
        if operation is None:
            raise ValueError(f"missing compiler operation: {opcode}")
        if operation.python_module and not _callable(
            operation.python_module + "." + operation.python_name
        ):
            raise ValueError(f"missing runtime operation: {opcode}")
    for node in plan.nodes.values():
        call = node.attributes.get("call")
        if call:
            key = (call["symbol_id"], call["overload_id"], call["call_form"])
            binding = target.call_bindings.get(key)
            if (
                binding
                and binding.python_module
                and not _callable(binding.python_module + "." + binding.python_name)
            ):
                raise ValueError(f"missing runtime callable: {key}")
        symbol = node.attributes.get("symbol_id")
        value = target.value_bindings.get(symbol) if symbol else None
        if (
            value
            and value.python_module
            and not _callable(value.python_module + "." + value.python_name)
        ):
            raise ValueError(f"missing runtime value: {symbol}")
