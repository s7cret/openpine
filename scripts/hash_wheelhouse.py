#!/usr/bin/env python3
"""Hash a wheelhouse. Does not tag or rewrite 4.0.2 identity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def collect_wheels(directory: Path) -> dict[str, str]:
    rows = {
        path.name: file_sha256(path)
        for path in sorted(directory.glob("*.whl"))
        if path.is_file()
    }
    if not rows:
        raise SystemExit("wheelhouse is empty")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheels = collect_wheels(args.wheelhouse)
    payload = {
        "schema": "openpine.wheelhouse.v1",
        "not_a_release": True,
        "wheels": wheels,
    }
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
