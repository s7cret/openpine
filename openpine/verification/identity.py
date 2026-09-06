"""Strict JSON artifacts for verification, with explicit domains and size bounds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def seal(body: dict) -> dict:
    if "content_hash" in body:
        raise ValueError("cannot seal an artifact containing an old content_hash")
    return {**body, "content_hash": digest(body)}


def verify(value: dict, schema: str) -> dict:
    if not isinstance(value, dict) or value.get("schema_id") != schema:
        raise ValueError("verification artifact schema mismatch")
    body = {k: v for k, v in value.items() if k != "content_hash"}
    if value.get("content_hash") != digest(body):
        raise ValueError("verification artifact hash mismatch")
    return body


def read_json(path: Path, limit: int = 32 * 1024 * 1024) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"nonfinite JSON value: {value}")

    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("verification input exceeds size limit")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")
