"""Bounded preload framing for the existing immutable request manifest API.

No alternate data model: the exact admitted manifest and effective configuration
are reconstructed before generated code runs. The spool bounds transport buffers,
not all source data, decoder allocations or broker memory.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
import hashlib
import json
import re
import tempfile
from typing import Any, TextIO

from openpine.runtime.request_data import MAX_MANIFEST_BYTES

SCHEMA = "openpine.request_preload.v1"
FRAME_BYTES = 256 * 1024
CHUNK_BYTES = 128 * 1024
SPOOL_BYTES = 2 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate request preload key")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f"nonfinite request preload value: {value}")


class RequestPreload:
    """Detached serialization; close even when spawning or writing fails."""

    def __init__(self, manifest: Mapping[str, Any]) -> None:
        self._spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES, mode="w+b")
        digest = hashlib.sha256()
        size = 0
        try:
            encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), allow_nan=False)
            for piece in encoder.iterencode(manifest):
                data = piece.encode("ascii")
                size += len(data)
                if size > MAX_MANIFEST_BYTES:
                    raise ValueError("request preload exceeds byte limit")
                digest.update(data)
                self._spool.write(data)
            self.descriptor = {
                "schema_id": SCHEMA,
                "size": size,
                "sha256": digest.hexdigest(),
                "manifest_hash": manifest["content_hash"],
                "execution_context_hash": manifest["execution_context_hash"],
            }
            self._spool.seek(0)
        except BaseException:
            self.close()
            raise

    def frames(self) -> Iterator[str]:
        self._spool.seek(0)
        sequence = 0
        while data := self._spool.read(CHUNK_BYTES):
            frame = _json(
                {
                    "kind": "REQUEST_CHUNK",
                    "sequence": sequence,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "data": base64.b64encode(data).decode("ascii"),
                }
            )
            if len(frame) > FRAME_BYTES:
                raise ValueError("request chunk exceeds frame limit")
            yield frame
            sequence += 1
        yield _json(
            {"kind": "REQUEST_PRELOAD_END", "chunks": sequence, "sha256": self.descriptor["sha256"]}
        )

    def close(self) -> None:
        self._spool.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def receive_request_manifest(stream: TextIO, descriptor: Mapping, context: Mapping) -> dict:
    if (
        not isinstance(descriptor, Mapping)
        or set(descriptor)
        != {"schema_id", "size", "sha256", "manifest_hash", "execution_context_hash"}
        or descriptor["schema_id"] != SCHEMA
        or type(descriptor["size"]) is not int
        or not 0 < descriptor["size"] <= MAX_MANIFEST_BYTES
        or type(descriptor["sha256"]) is not str
        or not _HEX.fullmatch(descriptor["sha256"])
        or descriptor["execution_context_hash"] != context["content_hash"]
    ):
        raise ValueError("invalid request preload descriptor")
    for name in ("manifest_hash", "execution_context_hash"):
        value = descriptor[name]
        if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("invalid request preload identity")
    digest, size, sequence = hashlib.sha256(), 0, 0
    with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES, mode="w+b") as spool:
        while True:
            line = stream.readline(FRAME_BYTES + 2)
            if not line or not line.endswith("\n") or len(line.encode("utf-8")) > FRAME_BYTES + 1:
                raise ValueError("truncated or oversized request preload frame")
            try:
                item = json.loads(line, object_pairs_hook=_unique, parse_constant=_reject_constant)
                if not isinstance(item, dict):
                    raise ValueError("request frame must be an object")
                if item.get("kind") == "REQUEST_PRELOAD_END":
                    if (
                        set(item) != {"kind", "chunks", "sha256"}
                        or type(item["chunks"]) is not int
                        or item["chunks"] != sequence
                        or item["sha256"] != descriptor["sha256"]
                        or size != descriptor["size"]
                        or digest.hexdigest() != item["sha256"]
                    ):
                        raise ValueError("request preload ended inconsistently")
                    break
                if (
                    set(item) != {"kind", "sequence", "sha256", "data"}
                    or item["kind"] != "REQUEST_CHUNK"
                    or type(item["sequence"]) is not int
                    or item["sequence"] != sequence
                    or type(item["data"]) is not str
                ):
                    raise ValueError("invalid request chunk sequence")
                data = base64.b64decode(item["data"], validate=True)
                if (
                    not 0 < len(data) <= CHUNK_BYTES
                    or hashlib.sha256(data).hexdigest() != item["sha256"]
                ):
                    raise ValueError("request chunk integrity mismatch")
                size += len(data)
                if size > descriptor["size"]:
                    raise ValueError("request preload exceeds admitted size")
                digest.update(data)
                spool.write(data)
                sequence += 1
            except (KeyError, TypeError, RecursionError) as exc:
                raise ValueError("malformed request preload frame") from exc
        spool.seek(0)
        try:
            manifest = json.load(spool, object_pairs_hook=_unique, parse_constant=_reject_constant)
        except (RecursionError, UnicodeError) as exc:
            raise ValueError("malformed request manifest") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("content_hash") != descriptor["manifest_hash"]
        or manifest.get("execution_context_hash") != descriptor["execution_context_hash"]
    ):
        raise ValueError("request manifest differs from preload identity")
    from openpine.runtime.request_data import request_provider_from_manifest

    request_provider_from_manifest(manifest, context)
    return manifest


def inflate_request_config(request: dict, stream: TextIO) -> None:
    """Run before HELLO, module execution or any ordinary protocol reads."""
    descriptor = request.get("request_preload")
    if descriptor is None:
        return
    config = request.get("engine_config")
    if not isinstance(config, dict) or "request_manifest" in config:
        raise ValueError("duplicate or missing request preload configuration")
    manifest = receive_request_manifest(stream, descriptor, request["execution_context"])
    candidate = {**config, "request_manifest": manifest}
    from openpine.runtime.rc6_config import effective_config_hash

    expected = candidate.get("effective_config_hash")
    settings = {key: value for key, value in candidate.items() if key != "effective_config_hash"}
    if expected != effective_config_hash(settings):
        raise ValueError("request preload effective configuration identity mismatch")
    request["engine_config"] = candidate
