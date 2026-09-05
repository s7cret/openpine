"""Bounded, integrity-checked bulk-result transport shared by both processes.

MessagePack preserves IEEE floats (including non-finite metric values) without
pickle, JSON NaN tokens, custom object hooks, or loading executable objects.
Chunks are individually checked and the final manifest binds the complete stream
to the admitted run, configuration and applied input registry. This is integrity
and identity validation, not a signature or proof of correct Pine semantics.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
from collections.abc import Iterator, Mapping
from typing import Any

import msgpack

SCHEMA = "openpine.bulk.result.v1"
CHUNK_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024 * 1024
SPOOL_MEMORY_BYTES = 2 * 1024 * 1024
_CHUNK_KEYS = {"kind", "sequence", "data", "size", "sha256"}
_END_KEYS = {"kind", "schema_id", "codec", "chunks", "payload_bytes", "payload_hash",
             "identity", "content_hash"}
_IDENTITY_KEYS = {"execution_context_hash", "effective_config_hash", "input_values_hash",
                  "input_registry_hash"}


class BulkResultError(ValueError):
    """A result is truncated, oversized, corrupted, or belongs to a different run."""


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "content_hash"}
    return _hash(json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("utf-8"))


def result_identity(context: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, str]:
    identity = {"execution_context_hash": context["content_hash"],
                **{name: raw[name] for name in _IDENTITY_KEYS - {"execution_context_hash"}}}
    if not all(_is_hash(value) for value in identity.values()):
        raise BulkResultError("result identity must contain SHA256 hashes")
    return identity


def _pack(value: Any, packer: msgpack.Packer, depth: int = 0) -> Iterator[bytes]:
    if depth > 100:
        raise BulkResultError("result nesting exceeds limit")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise BulkResultError("result mapping keys must be strings")
        yield packer.pack_map_header(len(value))
        for key in sorted(value):
            yield packer.pack(key)
            yield from _pack(value[key], packer, depth + 1)
    elif isinstance(value, (list, tuple)):
        yield packer.pack_array_header(len(value))
        for item in value:
            yield from _pack(item, packer, depth + 1)
    elif type(value) in (str, bool, int, float) or value is None:
        yield packer.pack(value)
    else:
        raise BulkResultError(f"unsupported result value type: {type(value).__name__}")


def encode_result(
    payload: Mapping[str, Any], *, identity: Mapping[str, str],
    chunk_bytes: int = CHUNK_BYTES, max_result_bytes: int = MAX_RESULT_BYTES,
) -> Iterator[dict[str, Any]]:
    """Serialize incrementally; never create a second full encoded-result buffer."""
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES:
        raise BulkResultError("invalid result chunk limit")
    if type(max_result_bytes) is not int or max_result_bytes < 1:
        raise BulkResultError("invalid result byte limit")
    if set(identity) != _IDENTITY_KEYS or not all(_is_hash(v) for v in identity.values()):
        raise BulkResultError("invalid result identity")
    digest, pending, sequence, total = hashlib.sha256(), bytearray(), 0, 0
    for piece in _pack(payload, msgpack.Packer(use_bin_type=True, strict_types=True)):
        view = memoryview(piece)
        while view:
            size = min(chunk_bytes - len(pending), len(view))
            pending.extend(view[:size])
            view = view[size:]
            total += size
            if total > max_result_bytes:
                raise BulkResultError("bulk result exceeds byte limit")
            if len(pending) == chunk_bytes:
                block = bytes(pending)
                digest.update(block)
                yield {"kind": "BULK_RESULT_CHUNK", "sequence": sequence,
                       "data": base64.b64encode(block).decode("ascii"),
                       "size": len(block), "sha256": _hash(block)}
                pending.clear()
                sequence += 1
    if pending:
        block = bytes(pending)
        digest.update(block)
        yield {"kind": "BULK_RESULT_CHUNK", "sequence": sequence,
               "data": base64.b64encode(block).decode("ascii"),
               "size": len(block), "sha256": _hash(block)}
        sequence += 1
    manifest = {"kind": "BULK_RESULT_END", "schema_id": SCHEMA, "codec": "msgpack.v1",
                "chunks": sequence, "payload_bytes": total,
                "payload_hash": "sha256:" + digest.hexdigest(), "identity": dict(identity)}
    manifest["content_hash"] = _manifest_hash(manifest)
    yield manifest


def _mapping(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise BulkResultError("result map has a duplicate or non-string key")
        result[key] = value
    return result


def _extension(_code: int, _data: bytes) -> Any:
    raise BulkResultError("result extensions are not supported")


class BulkResultReceiver:
    """Spool bounded chunks; decode exactly one object only after final validation."""

    def __init__(self, identity: Mapping[str, str], *, max_result_bytes: int = MAX_RESULT_BYTES,
                 memory_bytes: int = SPOOL_MEMORY_BYTES) -> None:
        if set(identity) != _IDENTITY_KEYS or not all(_is_hash(v) for v in identity.values()):
            raise BulkResultError("invalid expected result identity")
        if type(max_result_bytes) is not int or max_result_bytes < 1:
            raise BulkResultError("invalid result byte limit")
        if type(memory_bytes) is not int or memory_bytes < 1:
            raise BulkResultError("invalid result spool limit")
        self.identity = dict(identity)
        self.max_result_bytes = max_result_bytes
        self.stream = tempfile.SpooledTemporaryFile(max_size=memory_bytes, mode="w+b")
        self.digest = hashlib.sha256()
        self.chunks = self.size = 0
        self.finished = False
        self.manifest: dict[str, Any] | None = None

    def __enter__(self) -> BulkResultReceiver:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self.stream.close()

    def accept(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        if self.finished or self.stream.closed:
            raise BulkResultError("bulk result stream is already finished")
        try:
            return self._accept(message)
        except (ValueError, TypeError, KeyError, OverflowError, OSError, msgpack.UnpackException) as exc:
            self.finished = True
            self.close()
            if isinstance(exc, BulkResultError):
                raise
            raise BulkResultError(f"invalid bulk result: {exc}") from exc

    def _accept(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, Mapping):
            raise BulkResultError("result frame must be an object")
        if message.get("kind") == "BULK_RESULT_CHUNK":
            if (set(message) != _CHUNK_KEYS or type(message["sequence"]) is not int
                    or message["sequence"] != self.chunks or type(message["size"]) is not int
                    or not 1 <= message["size"] <= CHUNK_BYTES
                    or not isinstance(message["data"], str)
                    or len(message["data"]) > 4 * ((CHUNK_BYTES + 2) // 3)):
                raise BulkResultError("invalid result chunk sequence or size")
            if self.size + message["size"] > self.max_result_bytes:
                raise BulkResultError("bulk result exceeds byte limit")
            block = base64.b64decode(message["data"], validate=True)
            if len(block) != message["size"] or _hash(block) != message["sha256"]:
                raise BulkResultError("result chunk hash or size mismatch")
            self.stream.write(block)
            self.digest.update(block)
            self.size += len(block)
            self.chunks += 1
            return None
        if (set(message) != _END_KEYS or message.get("kind") != "BULK_RESULT_END"
                or message["schema_id"] != SCHEMA or message["codec"] != "msgpack.v1"
                or type(message["chunks"]) is not int or message["chunks"] != self.chunks
                or self.chunks == 0 or type(message["payload_bytes"]) is not int
                or message["payload_bytes"] != self.size
                or message["payload_hash"] != "sha256:" + self.digest.hexdigest()
                or message["identity"] != self.identity
                or message["content_hash"] != _manifest_hash(message)):
            raise BulkResultError("bulk result manifest identity, hash or completeness mismatch")
        self.stream.seek(0)
        unpacker = msgpack.Unpacker(
            self.stream, raw=False, strict_map_key=True, object_pairs_hook=_mapping,
            ext_hook=_extension, max_buffer_size=self.max_result_bytes,
            read_size=min(64 * 1024, self.max_result_bytes),
            max_array_len=min(self.max_result_bytes, 2_000_000),
            max_map_len=min(self.max_result_bytes, 100_000),
            max_str_len=self.max_result_bytes, max_bin_len=0, max_ext_len=0,
        )
        payload = unpacker.unpack()
        if unpacker.tell() != self.size or not isinstance(payload, dict):
            raise BulkResultError("result must contain exactly one mapping")
        self.finished = True
        self.manifest = dict(message)
        self.close()
        return payload
