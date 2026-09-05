from __future__ import annotations

import base64
import hashlib
import json
import math

import msgpack
import pytest

from openpine.runtime.bulk_result import (BulkResultError, BulkResultReceiver,
    encode_result, _manifest_hash)

IDENTITY = {key: "sha256:" + digit * 64 for key, digit in [
    ("execution_context_hash", "1"), ("effective_config_hash", "2"),
    ("input_values_hash", "3"), ("input_registry_hash", "4")]}


def receive(frames, **kwargs):
    with BulkResultReceiver(IDENTITY, **kwargs) as receiver:
        result = None
        for message in frames:
            result = receiver.accept(message)
        assert result is not None
        return result


def test_large_result_is_chunked_spooled_and_exact():
    payload = {"raw_result": {"status": "completed", "text": "Результат 🔥" * 120_000,
                              "curve": list(range(10_000))}, "intent_tape": []}
    frames = list(encode_result(payload, identity=IDENTITY))
    assert len(frames) > 5
    assert max(len(json.dumps(row).encode()) for row in frames) < 400_000
    with BulkResultReceiver(IDENTITY, memory_bytes=100) as receiver:
        for row in frames[:-1]:
            assert receiver.accept(row) is None
        assert receiver.stream._rolled is True
        actual = receiver.accept(frames[-1])
        assert receiver.stream.closed
    assert actual == payload


def test_special_float_values_and_zero_are_lossless():
    actual = receive(encode_result({"n": float("nan"), "p": float("inf"),
                                    "m": -float("inf"), "z": -0.0,
                                    "f": False, "t": "", "null": None}, identity=IDENTITY))
    assert math.isnan(actual["n"])
    assert actual["p"] == float("inf") and actual["m"] == -float("inf")
    assert math.copysign(1, actual["z"]) == -1
    assert actual["f"] is False and actual["t"] == "" and actual["null"] is None


@pytest.mark.parametrize("fault", ["sequence", "bool_sequence", "hash", "size", "base64",
                                    "unknown_key", "truncate", "context", "config", "inputs",
                                    "manifest_hash", "manifest_count", "old_raw_result"])
def test_corruption_reordering_identity_and_old_wire_format_fail_closed(fault):
    frames = list(encode_result({"value": "a" * 100}, identity=IDENTITY, chunk_bytes=16))
    if fault == "sequence": frames[0]["sequence"] = 1
    elif fault == "bool_sequence": frames[0]["sequence"] = False
    elif fault == "hash": frames[0]["sha256"] = "sha256:" + "f" * 64
    elif fault == "size": frames[0]["size"] += 1
    elif fault == "base64": frames[0]["data"] = "!!!"
    elif fault == "unknown_key": frames[0]["extra"] = 1
    elif fault == "truncate": del frames[-2]
    elif fault in {"context", "config", "inputs"}:
        key = {"context": "execution_context_hash", "config": "effective_config_hash",
               "inputs": "input_values_hash"}[fault]
        frames[-1]["identity"][key] = "sha256:" + "f" * 64
        frames[-1]["content_hash"] = _manifest_hash(frames[-1])
    elif fault == "manifest_hash": frames[-1]["content_hash"] = "sha256:" + "f" * 64
    elif fault == "manifest_count": frames[-1]["chunks"] = True
    else: frames = [{"kind": "BULK_RESULT", "raw_result": {"status": "completed"}}]
    with pytest.raises(BulkResultError):
        receive(frames)


def test_end_frame_is_mandatory_and_duplicate_end_is_rejected():
    frames = list(encode_result({"a": 1}, identity=IDENTITY))
    with BulkResultReceiver(IDENTITY) as receiver:
        for frame in frames[:-1]:
            assert receiver.accept(frame) is None
        assert not receiver.finished
        assert receiver.accept(frames[-1]) == {"a": 1}
        with pytest.raises(BulkResultError, match="already finished"):
            receiver.accept(frames[-1])


def test_oversize_rejected_by_sender_and_receiver_before_decode():
    with pytest.raises(BulkResultError, match="byte limit"):
        list(encode_result({"text": "x" * 100}, identity=IDENTITY, max_result_bytes=50))
    frames = list(encode_result({"text": "x" * 100}, identity=IDENTITY))
    with BulkResultReceiver(IDENTITY, max_result_bytes=50) as receiver:
        with pytest.raises(BulkResultError, match="byte limit"):
            receiver.accept(frames[0])
        assert receiver.stream.closed


def _hostile(data):
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    manifest = {"kind": "BULK_RESULT_END", "schema_id": "openpine.bulk.result.v1",
                "codec": "msgpack.v1", "chunks": 1, "payload_bytes": len(data),
                "payload_hash": digest, "identity": IDENTITY}
    manifest["content_hash"] = _manifest_hash(manifest)
    return [{"kind": "BULK_RESULT_CHUNK", "sequence": 0, "size": len(data),
             "sha256": digest, "data": base64.b64encode(data).decode()}, manifest]


@pytest.mark.parametrize("data", [b"\x82\xa1a\x01\xa1a\x02", b"\x81\x01\x02",
                                     b"\x80\x80", b"\xc1", b"\xdf\xff\xff\xff\xff",
                                     msgpack.packb(msgpack.ExtType(1, b"code")), b"\x91\x01"])
def test_hostile_msgpack_is_never_accepted(data):
    with pytest.raises(BulkResultError):
        receive(_hostile(data))


def test_serializer_is_deterministic_for_dictionary_insertion_order():
    a = list(encode_result({"b": 2, "a": [3, 4]}, identity=IDENTITY))
    b = list(encode_result({"a": [3, 4], "b": 2}, identity=IDENTITY))
    assert a == b


def test_receiver_closes_spool_on_caller_failure():
    with pytest.raises(RuntimeError):
        with BulkResultReceiver(IDENTITY, memory_bytes=1) as receiver:
            receiver.accept(next(encode_result({"a": 1}, identity=IDENTITY)))
            raise RuntimeError("cancelled")
    assert receiver.stream.closed


@pytest.mark.parametrize("value", [{1: "bad"}, {"a": object()}, {"a": b"binary"}])
def test_sender_rejects_non_contract_values(value):
    with pytest.raises(BulkResultError):
        list(encode_result(value, identity=IDENTITY))
