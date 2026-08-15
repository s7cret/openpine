#!/usr/bin/env python3
"""Emit a typed OpenAPI operation union for the UI client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def operations(schema: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for path, item in (schema.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in item:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                rows.append((method.upper(), str(path)))
    return sorted(set(rows))


def render(rows: list[tuple[str, str]]) -> str:
    lines = [
        "/* Generated from OpenAPI. Do not edit by hand. */",
        "export type OpenApiOperation =",
    ]
    for index, (method, path) in enumerate(rows):
        suffix = "" if index == len(rows) - 1 else " |"
        lines.append(f"  | {{ method: '{method}'; path: '{path}' }}{suffix}")
    lines.append("")
    lines.append("export const OPENAPI_OPERATION_COUNT = " + str(len(rows)))
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("schema", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(operations(schema)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
