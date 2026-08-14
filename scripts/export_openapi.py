#!/usr/bin/env python3
"""Export OpenPine's canonical OpenAPI document for UI contract checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpine.gateway.server import create_app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = create_app().openapi()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
