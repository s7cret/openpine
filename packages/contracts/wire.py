"""Strict finite UTF-8 JSON and atomic local persistence, without domain imports."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def loads(raw, *, max_bytes=16 * 1024 * 1024):
    if type(raw) is str:
        raw = raw.encode("utf-8")
    if type(raw) is not bytes or len(raw) > max_bytes:
        raise ValueError("JSON exceeds the input limit.")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def number(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("Nonfinite JSON number.")
        return value

    def constant(token):
        raise ValueError(f"Nonfinite JSON constant: {token}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=number, parse_constant=constant)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid JSON encoding or nesting.") from error


def read_json(path, *, max_bytes=16 * 1024 * 1024):
    with Path(path).open("rb") as stream:
        return loads(stream.read(max_bytes + 1), max_bytes=max_bytes)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
