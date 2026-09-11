"""Structured, original-source diagnostics; never guess a location from a name."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .source_context import PreparedSource

MAX_DIAGNOSTICS = 100


def frontend_diagnostics(rows, prepared: PreparedSource) -> list[dict[str, Any]]:
    result = []
    for row in list(rows)[:MAX_DIAGNOSTICS]:
        if not isinstance(row, Mapping):
            continue
        span = row.get("span")
        location = None
        if isinstance(span, Mapping) and type(span.get("start_offset")) is int:
            if prepared.linked is not None:
                location = prepared.linked.original_location(span["start_offset"])
            else:
                location = {
                    "source": prepared.source_name,
                    "line": span.get("start_line"),
                    "column": span.get("start_col"),
                    "offset": span["start_offset"],
                }
        result.append(
            {
                "code": str(row.get("code", "P2A_FRONTEND")),
                "message": str(row.get("message", ""))[:4000],
                "severity": str(row.get("severity", "ERROR")),
                "phase": "frontend",
                "location": location,
                "virtual_span": dict(span) if isinstance(span, Mapping) else None,
                "coordinate_basis": "normalized_pine_unicode",
            }
        )
    return result


def exception_diagnostics(
    exc: Exception, prepared: PreparedSource, *, phase: str, bundle: Mapping | None = None
) -> list[dict[str, Any]]:
    rows = getattr(exc, "diagnostics", None)
    if rows:
        return frontend_diagnostics(rows, prepared)
    finding = getattr(exc, "finding", None)
    code = getattr(exc, "code", None) or getattr(finding, "code", None) or "OPENPINE_COMPILE_ERROR"
    details = getattr(finding, "details", None)
    node_id = details.get("node_id") if isinstance(details, Mapping) else None
    if isinstance(node_id, str) and isinstance(bundle, Mapping):
        # Only exact IDs from the already-built bundle. Never guess from message
        # text, function spelling, caller offsets, or an untrusted sidecar.
        candidates = [
            row
            for row in bundle.get("node_index", [])
            if isinstance(row, Mapping) and row.get("node_id") == node_id
        ]
        if len(candidates) == 1 and isinstance(candidates[0].get("span"), Mapping):
            mapped = frontend_diagnostics(
                [
                    {
                        "code": code,
                        "message": str(getattr(finding, "message", exc)),
                        "span": candidates[0]["span"],
                    }
                ],
                prepared,
            )
            for row in mapped:
                row["phase"] = phase
            return mapped
    location = None
    source = getattr(exc, "source", None)
    if source is not None:
        location = {
            "source": source,
            "line": getattr(exc, "line", None),
            "column": getattr(exc, "column", None),
            "offset": None,
        }
    return [
        {
            "code": str(code),
            "message": str(getattr(exc, "message", getattr(finding, "message", exc)))[:4000],
            "severity": "ERROR",
            "phase": phase,
            "location": location,
            "virtual_span": None,
            "coordinate_basis": "normalized_pine_unicode",
        }
    ]


def format_diagnostic(item: Mapping[str, Any]) -> str:
    loc = item.get("location")
    prefix = ""
    if isinstance(loc, Mapping):
        prefix = str(loc.get("source", "<unknown>"))
        if loc.get("line") is not None:
            prefix += ":" + str(loc["line"])
        if loc.get("column") is not None:
            prefix += ":" + str(loc["column"])
        prefix += ": "
    return f"{prefix}{item['code']}: {item['message']}"
